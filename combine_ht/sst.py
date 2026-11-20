# Copyright (c) 2026 TileXR Project.
"""Compile the exact HT entry, run each phase of an SST case, and check its dumps."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
from types import SimpleNamespace

import yaml

import fixtures

WORK = Path(__file__).resolve().parent
TOOLS = WORK / "tooling/tools/unitest/simulator"
EXPERIMENT = TOOLS / "experiments/alltoall_udma"
sys.path[:0] = [str(TOOLS), str(EXPERIMENT)]
from case_tools import extract_die_output, extract_ranges, verified_die_decoder
from configuration import configure, V310_CONFIG
from kernel_build import find_linker
import runtime as rt
from sim_environment import RESOURCE_GROUPS, checksum, file_record, load_environment, runtime_fingerprint, shell_command, verify_environment

ENTRY = "tilexr_moonep_combine_ht_kernel"
KERNEL = WORK / (ENTRY + ".o")
ROOT = Path("/opt/sim")
POINTERS = ("workspace", "input", "forward", "forward_counts", "ranges", "masks", "backward",
            "backward_counts", "backward_ranges", "status", "profiling", "comm", "fullmesh_info", "registry")
CONFIG = ("rank", "world", "tokens", "hidden", "topk", "input_rows", "gather_rows", "chunk_tokens",
          "first_chunk_splits", "reduce_core_begin", "reduce_core_count", "run_mask", "wait_policy")
LAYOUT32 = ("chunks", "groups", "control_slots", "forward_capacity", "backward_capacity", "reserved")
LAYOUT64 = ("gather_offset", "partial_offset", "workspace_bytes", "backward_range_elements")
SIM_TIME = re.compile(r"Simulation is complete, simulated time:\s*([0-9.eE+-]+)\s+([munpf]?s)\b")
TIME_FACTORS = {"fs": 1e-9, "ps": 1e-6, "ns": 1e-3, "us": 1, "ms": 1e3, "s": 1e6}


def write_json(path, value):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(value, indent=2) + "\n")
    pending.replace(path)


def select_environment():
    config = json.loads((TOOLS / "environment.json").read_text())
    config["library_dir"] = "plugins"
    paths = {}
    for key, directory in RESOURCE_GROUPS.items():
        filename = "test_UBusSim.py" if key == "installed_model.py" else Path(key).name
        candidates = sorted((ROOT / "simulator" / directory).rglob(filename))
        perf = [path for path in candidates if "perf" in str(path.relative_to(ROOT)).lower()]
        if len(perf) == 1:
            paths[key] = str(perf[0])
        elif len(candidates) == 1 and (key == "installed_model.py" or directory == "UBusSim_deployment"):
            paths[key] = str(candidates[0])
        else:
            raise ValueError(f"expected one perf config for {key}, found {len(perf)}; candidates={candidates}")
    config["platform_resources"] = paths
    environment = WORK / "perf-environment.json"
    write_json(environment, config)
    env = load_environment(environment)
    sources = {key: Path(path) for key, path in paths.items()}
    snapshot = dict(settings=env, resources={key: file_record(path) for key, path in sources.items()},
                    runtime_files=runtime_fingerprint(env, sources))
    write_json(WORK / "environment.snapshot.json", snapshot)
    return snapshot


def build_kernel(env, log):
    src = WORK / "candidate"
    cann = Path(env["cann_root"])
    arch = cann / "aarch64-linux"
    compiler = Path(env["compiler"])
    linker = find_linker(cann, compiler)
    includes = [src / "include", src / "src/moonep/combine_ht/common",
                cann / "compiler/tikcpp", cann / "compiler/tikcpp/tikcfw",
                cann / "compiler/tikcpp/tikcfw/impl", cann / "compiler/tikcpp/tikcfw/interface",
                arch / "ascendc/include", arch / "asc/include", arch / "include",
                arch / "pkg_inc", arch / "pkg_inc/runtime"]
    obj = KERNEL.with_name(KERNEL.stem + "_rel.o")
    options = ["-xcce", "-O2", "-DCATLASS_ARCH=3510", "--cce-auto-sync", "-Wno-deprecated-declarations",
               "--cce-aicore-arch=dav-c310-vec", "--cce-aicore-only", "-std=gnu++17",
               "-mllvm", "-cce-aicore-stack-size=0x8000", "-mllvm", "-cce-aicore-function-stack-size=0x8000",
               "-mllvm", "-cce-aicore-record-overflow=true", "-mllvm", "-cce-aicore-addr-transform",
               "-mllvm", "-cce-aicore-dcci-insert-for-scalar=false"]
    compile_command = [compiler, *options, *["-I" + str(p) for p in includes], "-c",
                       src / "src/moonep/combine_ht/kernels/combine_ht.cpp", "-o", obj]
    link_command = [linker, "-m", "aicorelinux", "-Ttext=0", obj, "--static",
                    "--allow-multiple-definition", "-o", KERNEL]
    write_json(WORK / "build.json", [list(map(str, argv)) for argv in (compile_command, link_command)])
    for argv in (compile_command, link_command):
        subprocess.run(shell_command(env, "cann", argv), check=True, timeout=60, stdout=log,
                       stderr=subprocess.STDOUT)
    subprocess.run(["readelf", "-sW", KERNEL], check=True, timeout=15, stdout=log, stderr=subprocess.STDOUT)
    if not KERNEL.read_bytes().startswith(b"\x7fELF"):
        raise ValueError("invalid AICore ELF")


def setup():
    snapshot = select_environment()
    with (WORK / "build.log").open("wb") as log:
        build_kernel(snapshot["settings"], log)
    return dict(ok=True, kernel_sha256=checksum(KERNEL), perf_resources=snapshot["resources"],
                build_report=str(WORK / "build.json"),
                model_libraries={name: record for name, record in snapshot["runtime_files"].items()
                                 if name.endswith(".so")})


def region_images(rank, images):
    base = rank * rt.GM_BYTES
    addresses = {}
    cursor = base + 0x200000000
    for name, image in images.items():
        addresses[name] = cursor
        cursor += fixtures.align(max(len(image), 1))
    if cursor >= base + rt.BASES["comm_args"]:
        raise ValueError("HT data overlaps simulator runtime GM")
    return addresses


def registry_image(records):
    data = bytearray(16 + 256 * 16)
    struct.pack_into("<4I", data, 0, 0x54585255, 1, len(records), 1)
    for index, record in enumerate(records):
        struct.pack_into("<QQ", data, 16 + index * 16, record["workspace_addr"], record["workspace_bytes"])
    return bytes(data)


def param_table(addresses, spec, mode):
    params = {}
    for n, name in enumerate(POINTERS):
        address = addresses.get(name, 0)
        params[name] = dict(value=address, param_offset=str(n * 8), param_type="11")
    for fields, vals, start, width, code in (
        (CONFIG, spec.config(mode), 112, 4, 5),
        (LAYOUT32, spec.layout()[:6], 168, 4, 5),
        (LAYOUT64, spec.layout()[6:], 192, 8, 7),
    ):
        for n, (name, value) in enumerate(zip(fields, vals)):
            params[name] = dict(value=value, param_offset=str(start + width * n), param_type=str(code))
    for name, value, offset, code in (("comm_peer_begin", spec.rank // 8 * 8, 164, 5),
                                      ("magic", 0, 224, 6), ("wqe_snapshot", 0, 232, 11)):
        params[name] = dict(value=value, param_offset=str(offset), param_type=str(code))
    return params


def create_workload(case, specs, prepared, mode, send_state=None):
    case.mkdir()
    output = {"workload_config": {}}
    records = []
    for spec, (images, _, _) in zip(specs, prepared):
        addresses = region_images(spec.rank, {**images, "status": bytes(512)})
        records.append(dict(rank=spec.rank, addresses=addresses, input_addr=addresses["input"],
                            workspace_addr=addresses["workspace"], workspace_bytes=spec.layout()[8]))
    scenario = dict(rank_size=len(specs), ranks=records,
                    layout=dict(workspace_bytes=max(r["workspace_bytes"] for r in records)))
    registry = registry_image(records)
    for spec, (initial, _, _), record in zip(specs, prepared, records):
        rank = spec.rank
        base = rank * rt.GM_BYTES
        directory = case / "input_bin" / f"rank{rank}"
        directory.mkdir(parents=True)
        images = {name: payload for name, payload in initial.items()
                  if name in (("input", "workspace", "backward", "backward_ranges") if mode == 1
                              else ("input", "workspace", "forward", "ranges", "masks"))}
        addresses = {name: record["addresses"][name] for name in images}
        if mode == 1:
            runtime = rt.runtime_images(rank, scenario)
            for name in ("sq_ctx", "cq_ctx", "mem_info", "eid_table", "queue_state", "cq_doorbells", "udma_info"):
                images[name] = runtime[name]
                addresses[name] = base + rt.BASES[name]
            images["registry"] = registry
            addresses["registry"] = base + rt.BASES["profile_registry"]
            addresses["fullmesh_info"] = addresses["udma_info"]
            images["status"] = b"\xff" * 32 + bytes(480)
            addresses["status"] = record["addresses"]["status"]
        else:
            reused = send_state[rank]
            images["workspace"] = reused.read_bytes()
        params = param_table(addresses, spec, mode)
        offset = 240
        for name, payload in images.items():
            path = directory / (name + ".bin")
            path.write_bytes(payload.ljust(fixtures.align(len(payload)), b"\0"))
            params["load_" + name] = dict(value=addresses[name], param_offset=str(offset),
                                         param_type="11", input_bin=str(path))
            offset += 8
        outputs = dict(workspace=dict(addr=addresses["workspace"], size=spec.layout()[8]))
        if mode == 1:
            outputs["status"] = dict(addr=addresses["status"], size=512)
        output["workload_config"][f"chip{rank}"] = dict(gm_size=rt.GM_BYTES, sys_va_base_addr=base,
            ub_base_addr=0x80000, stack_base_addr=0x100000, param_base_addr=base + 0x80000000,
            block_dim=64, subblock_dim=1, kernel_file=str(KERNEL), vec_func=ENTRY, kernel_param=params,
            bin_param=dict(addr=base + 0x40000000, size=max(0x26C40, fixtures.align(KERNEL.stat().st_size), offset)),
            output_param=outputs)
    return output


def write_model(case, snapshot, workload, ranks):
    sources = {name: Path(record["path"]) for name, record in snapshot["resources"].items()}
    args = SimpleNamespace(rank_size=ranks, log_level=4, instruction_dump_chips=[])
    configs, level = configure(sources, args)
    for name, config in configs.items():
        path = case / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(config, sort_keys=False))
    text = yaml.safe_dump(workload, sort_keys=False)
    (case / "workload.yml").write_text(text)
    (case / "v310_config/workload_config.yml").write_text(text)
    shutil.copyfile(sources["installed_model.py"], case / "installed_model.py")
    shutil.copyfile(EXPERIMENT / "model_entry.py", case / "test_UBusSim.py")
    write_json(case / "environment.snapshot.json", snapshot)
    return dict(su=configs[V310_CONFIG]["simulator_config"]["su"]["component"], lsu_level=level)


def read_output(bin_dir, rank, output_name, address, size, use_die_decoder):
    paths = [bin_dir / f"david_{rank}_die{die}_{output_name}.bin" for die in (1, 2)]
    local = address - rank * rt.GM_BYTES
    if use_die_decoder:
        return extract_die_output(paths, output_name, local, size)
    return bytes(extract_ranges(paths, {output_name: (local, size)})[output_name])


def validate(case, workload, prepared, mode):
    verified = verified_die_decoder(case) is not None
    retained = {}
    for rank, (_, golden_send, golden_reduce) in enumerate(prepared):
        outputs = workload["workload_config"][f"chip{rank}"]["output_param"]
        workspace = outputs["workspace"]
        actual = read_output(case / "bin", rank, "workspace", workspace["addr"], workspace["size"], verified)
        expected = golden_send if mode == 1 else golden_reduce
        if mode == 1:
            status = outputs["status"]
            raw = read_output(case / "bin", rank, "status", status["addr"], status["size"], verified)
            if any(raw):
                raise ValueError(f"rank {rank} nonzero kernel status: {struct.unpack_from('<8I', raw)}")
        if actual != expected:
            offset = next(i for i, (a, b) in enumerate(zip(actual, expected)) if a != b)
            raise ValueError(f"rank {rank} workspace mismatch byte {offset}, actual {actual[offset]}, expected {expected[offset]}")
        if mode == 1:
            path = case / f"rank{rank}.verified-workspace.bin"
            path.write_bytes(actual)
            retained[rank] = path
    return retained


def run_phase(case, mode, spec, prepared, config, args, stages, send_state=None):
    work = create_workload(case, spec, prepared, mode, send_state)
    with (case / "sim.log").open("wb") as log:
        model = write_model(case, config, work, args["ranks"])
        env = config["settings"]
        command = shell_command(env, "sst", [env["sst_bin"], "-n", str(args["threads"]),
                                           case / "test_UBusSim.py"])
        started = time.monotonic()
        try:
            process = subprocess.run(command, cwd=case, stdout=log, stderr=subprocess.STDOUT,
                                     timeout=args["stage_timeout"])
            code = process.returncode
        except subprocess.TimeoutExpired:
            code = 124
        elapsed = time.monotonic() - started
    result = dict(stage=case.name, wall_s=elapsed, returncode=code, model=model, report=str(case))
    stages.append(result)
    finish = []
    fatal = False
    with (case / "sim.log").open(errors="replace") as log:
        for line in log:
            match = SIM_TIME.search(line)
            if match:
                finish.append(float(match[1]) * TIME_FACTORS[match[2]])
            fatal |= "FATAL:" in line or "Traceback (most recent call last)" in line
    if finish:
        result["simulated_us"] = finish[-1]
    try:
        if code or fatal or not finish or finish[-1] <= 0:
            raise ValueError("SST did not complete successfully")
        state = validate(case, work, prepared, mode)
        result["verified_ranks"] = len(spec)
        return state
    finally:
        write_json(case / "run.json", result)


def run(args, stages):
    ranks, tokens, hidden = args["ranks"], args["tokens"], args["hidden"]
    if ranks not in (8, 16, 32, 64) or tokens < 1 or hidden < 16 or hidden % 16:
        raise ValueError("invalid fixture shape")
    case = WORK / "cases" / (f"{ranks}p-bs{tokens}-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    case.mkdir(parents=True)
    progress = dict(state="preparing", case=str(case), ranks=ranks, bs=tokens)
    write_json(WORK / "progress.json", progress)
    config = json.loads((WORK / "environment.snapshot.json").read_text())
    verify_environment(config)
    spec, routes = fixtures.prepare(ranks, tokens, hidden, 16, min(tokens, 4), 40, 64)
    routes_bytes = json.dumps(routes, separators=(",", ":")).encode()
    (case / "topk64p.json").write_bytes(routes_bytes)
    layouts = [rank.layout() for rank in spec]
    total_workspace = sum(layout[8] for layout in layouts)
    total_inputs = sum((len(rank.input_rows) or 1) * hidden * 2 for rank in spec)
    total_backward = 8 * layouts[0][4] * 24 * ranks
    if shutil.disk_usage(case).free < 10 * total_workspace + 2 * total_inputs + total_backward + 512 * 1024**2:
        raise ValueError("insufficient space for input images, retained workspace and per-Die dumps")
    prepared = [rank.serialize() for rank in spec]
    send_rows = sum(len(rank.gather_rows) for rank in spec)
    record = dict(ranks=ranks, source_ranks=64, bs=tokens, topk=16, hidden=hidden, reducers=40,
                  chunk=16, splits=min(tokens, 4), send_rows=send_rows, route_seed=64,
                  route_sha256=hashlib.sha256(routes_bytes).hexdigest(),
                  kernel_sha256=checksum(KERNEL), report=str(case), sst_threads=args["threads"], stages=stages)
    write_json(case / "case.json", record)
    for phase, mode in (("send-only", 1), ("reduce-only", 2)):
        write_json(WORK / "progress.json", {**progress, "state": phase, "stages": stages})
        if mode == 1:
            send_state = run_phase(case / phase, mode, spec, prepared, config, args, stages)
        else:
            run_phase(case / phase, mode, spec, prepared, config, args, stages, send_state)
        write_json(case / "case.json", record)
    write_json(WORK / "progress.json", {**progress, "state": "complete", "stages": stages})
    return {**record, "ok": True}


def main(args):
    stages = []
    try:
        result = setup() if args["stage"] == "setup" else run(args, stages)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "stage": args["stage"], "error": type(exc).__name__,
                  "detail": str(exc)[:2200], "stages": stages}
        if args["stage"] == "run" and (WORK / "progress.json").is_file():
            previous = json.loads((WORK / "progress.json").read_text())
            write_json(WORK / "progress.json", {**previous, "state": "failed", "result": result})
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
