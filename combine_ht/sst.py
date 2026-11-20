# Copyright (c) 2026 TileXR Project.
"""Compile the exact HT entry, run each phase of an SST case, and check its dumps."""

from contextlib import redirect_stdout
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
from configuration import configure, UBUS_CONFIG, V310_CONFIG
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


def build_kernel(env, log, source=None, binary=KERNEL):
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
    source = source or src / "src/moonep/combine_ht/kernels/combine_ht.cpp"
    obj = binary.with_name(binary.stem + "_rel.o")
    options = ["-xcce", "-O2", "-DCATLASS_ARCH=3510", "--cce-auto-sync", "-Wno-deprecated-declarations",
               "--cce-aicore-arch=dav-c310-vec", "--cce-aicore-only", "-std=gnu++17",
               "-mllvm", "-cce-aicore-stack-size=0x8000", "-mllvm", "-cce-aicore-function-stack-size=0x8000",
               "-mllvm", "-cce-aicore-record-overflow=true", "-mllvm", "-cce-aicore-addr-transform",
               "-mllvm", "-cce-aicore-dcci-insert-for-scalar=false"]
    compile_command = [compiler, *options, *["-I" + str(p) for p in includes], "-c",
                       source, "-o", obj]
    link_command = [linker, "-m", "aicorelinux", "-Ttext=0", obj, "--static",
                    "--allow-multiple-definition", "-o", binary]
    write_json(binary.parent / "build.json", [list(map(str, argv)) for argv in (compile_command, link_command)])
    for argv in (compile_command, link_command):
        subprocess.run(shell_command(env, "cann", argv), check=True, timeout=60, stdout=log,
                       stderr=subprocess.STDOUT)
    subprocess.run(["readelf", "-sW", binary], check=True, timeout=15, stdout=log, stderr=subprocess.STDOUT)
    if not binary.read_bytes().startswith(b"\x7fELF"):
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
                                      ("magic", 0, 224, 6), ("wqe_snapshot", addresses.get("wqe_snapshot", 0), 232, 11)):
        params[name] = dict(value=value, param_offset=str(offset), param_type=str(code))
    return params


def create_workload(case, specs, prepared, mode, send_state=None, trace_send=False):
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
            images["status"] = b"\x5a" * 32 + bytes(480)
            addresses["status"] = record["addresses"]["status"]
            if trace_send and rank == 2:
                snap_rows = 8 * (spec.backward_capacity + spec.chunks)
                images["wqe_snapshot"] = bytes(fixtures.align(snap_rows * 64))
                addresses["wqe_snapshot"] = base + 0x371000000
        else:
            images["workspace"] = (send_state[rank].read_bytes() if send_state is not None
                                   else prepared[rank][1])
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
            if rank == (2 if trace_send else 0):
                outputs["queueState"] = dict(addr=addresses["queue_state"],
                                             size=fixtures.align(len(images["queue_state"])))
            if trace_send and rank == 2:
                queue = rt.queue_addresses(rank, len(specs), 0, 0)
                entries = sum(len(items) + bool(items) for items in spec.backward[0])
                outputs["sqTrace"] = dict(addr=queue["sq"], size=fixtures.align(entries * 64))
                outputs["cqTrace"] = dict(addr=queue["cq"], size=512)
                payload_entries = sum(len(items) for items in spec.backward[0])
                outputs["wqeSnapshot"] = dict(addr=addresses["wqe_snapshot"],
                                               size=fixtures.align(payload_entries * 64))
        output["workload_config"][f"chip{rank}"] = dict(gm_size=rt.GM_BYTES, sys_va_base_addr=base,
            ub_base_addr=0x80000, stack_base_addr=0x100000, param_base_addr=base + 0x80000000,
            block_dim=64, subblock_dim=1, kernel_file=str(KERNEL), vec_func=ENTRY, kernel_param=params,
            bin_param=dict(addr=base + 0x40000000, size=max(0x26C40, fixtures.align(KERNEL.stat().st_size), offset)),
            output_param=outputs)
    return output


def write_model(case, snapshot, workload, ranks):
    sources = {name: Path(record["path"]) for name, record in snapshot["resources"].items()}
    args = SimpleNamespace(rank_size=ranks, log_level=1, instruction_dump_chips=[])
    configs, level = configure(sources, args)
    configs[UBUS_CONFIG]["simulator_info"]["clos_router_in_fm_flag"] = True
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
    return dict(su=configs[V310_CONFIG]["simulator_config"]["su"]["component"],
                lsu_level=level, clos_router_in_fm_flag=True, log_level=args.log_level)


# V1.1.0 AddrAnalyzerV310::getDdieId uses the same address bits as V1.0.0.
V11_DIE_DECODER_SHA256 = "bf93fd6d0749fc3c6eaae365c7cba558988e1fa25c1b9a26e139be61b60a3da3"


def die_index(address):
    return ((address >> 9) ^ (address >> 16) ^ (address >> 23)) & 1


def has_die_decoder(case):
    if verified_die_decoder(case) is not None:
        return True
    snapshot = json.loads((case / "environment.snapshot.json").read_text())
    path = Path(snapshot["settings"]["library_dir"]) / "libDavinciSimulator.so"
    record = snapshot["runtime_files"][str(path)]
    return record["sha256"] == V11_DIE_DECODER_SHA256 and checksum(path) == V11_DIE_DECODER_SHA256


def read_queue_output(bin_dir, rank, output_name, address, size):
    local = address - rank * rt.GM_BYTES
    records = {}
    for die in (0, 1):
        dump = (bin_dir / f"david_{rank}_die{die + 1}_{output_name}.bin").read_bytes()
        if len(dump) % 520:
            raise ValueError("incomplete addressed queue dump")
        for offset in range(0, len(dump), 520):
            addr = struct.unpack_from("<Q", dump, offset)[0]
            if die_index(addr) != die:
                continue
            data = dump[offset + 8:offset + 520]
            if addr in records and records[addr] != data:
                raise ValueError("conflicting queue dump records")
            records[addr] = data
    try:
        return b"".join(records[addr] for addr in range(local, local + size, 512))
    except KeyError as error:
        raise ValueError(f"missing queue block at {error.args[0]:#x}") from error


def read_output(bin_dir, rank, output_name, address, size, use_die_decoder):
    paths = [bin_dir / f"david_{rank}_die{die}_{output_name}.bin" for die in (1, 2)]
    local = address - rank * rt.GM_BYTES
    if use_die_decoder and output_name in ("sqTrace", "cqTrace"):
        return read_queue_output(bin_dir, rank, output_name, address, size)
    if use_die_decoder:
        return extract_die_output(paths, output_name, local, size)
    return bytes(extract_ranges(paths, {output_name: (local, size)})[output_name])


def queue_state(case, workload, ranks):
    item = workload["workload_config"]["chip0"]["output_param"]["queueState"]
    data = read_output(case / "bin", 0, "queueState", item["addr"], item["size"],
                       has_die_decoder(case))
    return [
        [struct.unpack_from("<I", data, rt.queue_addresses(0, ranks, peer, peer)[field] - item["addr"])[0]
         for field in ("sq_head", "sq_tail", "count", "cq_tail")]
        for peer in range(1, 8)
    ]


def diagnose_send(case, workload, specs, prepared):
    chip = workload["workload_config"]["chip2"]
    outputs = chip["output_param"]
    verified = has_die_decoder(case)
    sq = read_output(case / "bin", 2, "sqTrace", outputs["sqTrace"]["addr"],
                     outputs["sqTrace"]["size"], verified)
    cq = read_output(case / "bin", 2, "cqTrace", outputs["cqTrace"]["addr"], 512, verified)
    state = outputs["queueState"]
    data = read_output(case / "bin", 2, "queueState", state["addr"], state["size"], verified)
    queue = rt.queue_addresses(2, len(specs), 0, 0)
    frontier = {field: struct.unpack_from("<I", data, queue[field] - state["addr"])[0]
                for field in ("sq_head", "sq_tail", "count", "cq_tail")}
    src = chip["kernel_param"]
    dst = workload["workload_config"]["chip0"]
    target = dst["kernel_param"]
    target_mem = dst["output_param"]["workspace"]
    workspace = read_output(case / "bin", 0, "workspace", target_mem["addr"], target_mem["size"], verified)
    expected_gather = prepared[0][1]
    index = 0
    snap_index = 0
    snapshot = None
    if "wqeSnapshot" in outputs:
        item = outputs["wqeSnapshot"]
        snapshot = read_output(case / "bin", 2, "wqeSnapshot", item["addr"], item["size"], verified)
    entries = []
    for chunk, batch in enumerate(specs[2].backward[0]):
        rows = [(s, r, p & 0xFFFFFFFF) for s, r, p in batch]
        if not rows:
            continue
        rows.append((-1, -1, -1))
        for pos, (source, row, gather) in enumerate(rows):
            word0, word1 = struct.unpack_from("<II", sq, index * 64)
            remote, size, local = struct.unpack_from("<QI4xQ", sq, index * 64 + 40)
            so = source < 0
            if so:
                expected_src = src["workspace"]["value"] + 3 * 64 * 512
                offset = (3 * 64 + 96 + chunk * 8 + 2) * 512
                size_expected, expected_flag = 8, 0x22
            else:
                expected_src = src["input"]["value"] + row * 512
                offset = specs[0].layout()[6] + gather * 512
                size_expected = 512
                expected_flag = 0x20 if pos == len(rows) - 2 else 0
            expected_dest = target["workspace"]["value"] + offset
            observed_flag = (word0 >> 16) & 0xFF
            descriptor_ok = (remote, local, size, word0 & 0xFFFF, observed_flag) == (
                expected_dest, expected_src, size_expected, index, expected_flag)
            received = workspace[offset:offset + size_expected] == expected_gather[offset:offset + size_expected]
            entries.append(dict(entry=index, chunk=chunk, source=source, input_row=row, gather_row=gather,
                descriptor_ok=descriptor_ok, received=received, opcode=(word1 >> 8) & 0xFF,
                length=size, flag=observed_flag, local=hex(local), remote=hex(remote)))
            if snapshot is not None and not so:
                snap = snapshot[snap_index * 64:(snap_index + 1) * 64]
                snap0, snap1 = struct.unpack_from("<II", snap)
                entries[-1]["ub_snapshot_matches"] = (
                    *struct.unpack_from("<QI4xQ", snap, 40), snap0 & 0xFFFF,
                    (snap0 >> 16) & 0xFF, (snap1 >> 8) & 0xFF) == (
                    expected_dest, size_expected, expected_src, index, expected_flag, 3)
                snap_index += 1
            index += 1
    completions = []
    for index in range(len(cq) // 64):
        word0, word1 = struct.unpack_from("<II", cq, index * 64)
        if word0 or word1:
            completions.append(dict(owner=(word0 >> 2) & 1, entry=word1 & 0xFFFF,
                                    status=word0 >> 24, substatus=(word0 >> 16) & 0xFF))
    return dict(direction="rank2->rank0", frontier=frontier, descriptors=entries, completions=completions)


def validate(case, workload, prepared, mode):
    verified = has_die_decoder(case)
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
    trace_send = args["stage"] == "send-diag"
    work = create_workload(case, spec, prepared, mode, send_state, trace_send)
    with (case / "model.log").open("w") as log, redirect_stdout(log):
        model = write_model(case, config, work, args["ranks"])
    with (case / "sim.log").open("wb") as log:
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
    if mode == 2:
        result["input_origin"] = "verified_send_dump" if send_state is not None else "host_fixture"
    stages.append(result)
    finish = []
    fatal = False
    with (case / "sim.log").open("rb") as log:
        log.seek(max(0, (case / "sim.log").stat().st_size - 2**20))
        for line in log.read(2**20).decode(errors="replace").splitlines():
            match = SIM_TIME.search(line)
            if match:
                finish.append(float(match[1]) * TIME_FACTORS[match[2]])
            fatal |= "FATAL:" in line or "Traceback (most recent call last)" in line
    if finish:
        result["simulated_us"] = finish[-1]
    try:
        if code or fatal or not finish or finish[-1] <= 0:
            raise ValueError("SST did not complete successfully")
        if trace_send:
            result["send_trace"] = diagnose_send(case, work, spec, prepared)
        state = validate(case, work, prepared, mode)
        result["verified_ranks"] = len(spec)
        return state
    except (OSError, ValueError) as error:
        result["validation_error"] = str(error)
        if mode == 1 and code == 0 and finish and not trace_send:
            try:
                result["rank0_queue_head_tail_count_cqtail"] = queue_state(case, work, len(spec))
            except (OSError, ValueError) as queue_error:
                result["queue_read_error"] = str(queue_error)
        raise
    finally:
        write_json(case / "run.json", result)


def run(args, stages):
    ranks, tokens, hidden = args["ranks"], args["tokens"], args["hidden"]
    if ranks not in (8, 16, 32, 64) or tokens < 1 or hidden < 16 or hidden % 16:
        raise ValueError("invalid fixture shape")
    case = WORK / "cases" / (f"{ranks}p-bs{tokens}-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    case.mkdir(parents=True)
    progress = dict(state="preparing", case=str(case), ranks=ranks, bs=tokens, hidden=hidden)
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
    phases = (("reduce-only", 2),) if args["stage"] == "reduce-fixture" else (("send-only", 1), ("reduce-only", 2))
    if args["stage"] in ("send-diag", "send-only"):
        phases = (("send-only", 1),)
    send_state = None
    for phase, mode in phases:
        write_json(WORK / "progress.json", {**progress, "state": phase, "stages": stages})
        if mode == 1:
            try:
                send_state = run_phase(case / phase, mode, spec, prepared, config, args, stages)
            except (OSError, ValueError):
                if args["stage"] not in ("pair", "send-diag") or not stages or stages[-1]["returncode"] == 124:
                    raise
        else:
            try:
                run_phase(case / phase, mode, spec, prepared, config, args, stages, send_state)
            except (OSError, ValueError):
                if args["stage"] != "pair" or not stages or stages[-1]["stage"] != phase:
                    raise
        write_json(case / "case.json", record)
    measured = all(stage["returncode"] == 0 and stage.get("simulated_us", 0) > 0 for stage in stages)
    verification = all(stage.get("verified_ranks") == ranks for stage in stages)
    state = "complete" if measured else "incomplete"
    record.update(all_outputs_verified=verification,
                  ok=measured and (args["stage"] != "send-diag" or "send_trace" in stages[0]))
    write_json(case / "case.json", record)
    write_json(WORK / "progress.json", {**progress, "state": state, "stages": stages})
    return record


def main(args):
    stages = []
    try:
        result = setup() if args["stage"] == "setup" else run(args, stages)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "stage": args["stage"], "error": type(exc).__name__,
                  "detail": str(exc)[:2200], "stages": stages}
        if args["stage"] in ("run", "reduce-fixture", "pair", "send-diag", "send-only") and (WORK / "progress.json").is_file():
            previous = json.loads((WORK / "progress.json").read_text())
            write_json(WORK / "progress.json", {**previous, "state": "failed", "result": result})
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
