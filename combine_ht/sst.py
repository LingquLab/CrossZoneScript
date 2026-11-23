# Copyright (c) 2026 TileXR Project.
"""Compile the pinned HT entry and provide SST workload and dump-check helpers."""

from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import struct
import subprocess
import sys
import time
from types import SimpleNamespace

import yaml

import fixtures
import profile_dump

WORK = Path(__file__).resolve().parent
TOOLS = WORK / "tooling/tools/unitest/simulator"
EXPERIMENT = TOOLS / "experiments/alltoall_udma"
sys.path[:0] = [str(TOOLS), str(EXPERIMENT)]
from case_tools import extract_die_output
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
V11_DIE_DECODER_SHA256 = "bf93fd6d0749fc3c6eaae365c7cba558988e1fa25c1b9a26e139be61b60a3da3"
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
               "-DTILEXR_COMBINE_HT_SIM_SEND=1", "-DTILEXR_ENABLE_PROFILING=1", "--cce-aicore-arch=dav-c310-vec", "--cce-aicore-only", "-std=gnu++17",
               "-mllvm", "-cce-aicore-stack-size=0x8000", "-mllvm", "-cce-aicore-function-stack-size=0x8000",
               "-mllvm", "-cce-aicore-record-overflow=true", "-mllvm", "-cce-aicore-addr-transform",
               "-mllvm", "-cce-aicore-dcci-insert-for-scalar=false"]
    compile_command = [compiler, *options, *["-I" + str(p) for p in includes], "-c",
                       source, "-o", obj]
    link_command = [linker, "-m", "aicorelinux", "-Ttext=0", obj, "--static",
                    "--allow-multiple-definition", "-o", binary]
    write_json(binary.parent / "build.json", [list(map(str, argv)) for argv in (compile_command, link_command)])
    for argv in (compile_command, link_command):
        subprocess.run(shell_command(env, "cann", argv), check=True, timeout=150, stdout=log,
                       stderr=subprocess.STDOUT)
    subprocess.run(["readelf", "-sW", binary], check=True, timeout=15, stdout=log, stderr=subprocess.STDOUT)
    if not binary.read_bytes().startswith(b"\x7fELF"):
        raise ValueError("invalid AICore ELF")


def setup():
    snapshot = select_environment()
    with (WORK / "build.log").open("wb") as log:
        build_kernel(snapshot["settings"], log)
    return dict(ok=True, profiling_compiled=True, kernel_sha256=checksum(KERNEL), perf_resources=snapshot["resources"],
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


def create_workload(case, specs, prepared, mode, send_state=None):
    case.mkdir()
    output = {"workload_config": {}}
    records = []
    for spec, (images, _, _) in zip(specs, prepared):
        addresses = region_images(spec.rank, {**images, "status": bytes(512)})
        addresses["profiling"] = spec.rank * rt.GM_BYTES + 0x2E0000000
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
        else:
            assert send_state is not None, "reduce_requires_verified_send_output"
            images["workspace"] = send_state[rank].read_bytes()
        cap = profile_dump.capacity(spec)
        addresses["profiling"] = record["addresses"]["profiling"]
        images["profiling"] = profile_dump.image(addresses["profiling"], cap)
        params = param_table(addresses, spec, mode)
        offset = 240
        for name, payload in images.items():
            path = directory / (name + ".bin")
            path.write_bytes(payload.ljust(fixtures.align(len(payload)), b"\0"))
            params["load_" + name] = dict(value=addresses[name], param_offset=str(offset),
                                         param_type="11", input_bin=str(path))
            offset += 8
        outputs = dict(workspace=dict(addr=addresses["workspace"], size=spec.layout()[8]))
        outputs["profiling"] = dict(addr=addresses["profiling"], size=len(images["profiling"]))
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
    clock_fields = {}
    def clock_values(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                clock_values(item, path + "/" + str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                clock_values(item, path + "/" + str(index))
        elif re.search(r"clock|freq|timer|sys_cnt", path, re.I) and len(clock_fields) < 64:
            clock_fields[path] = str(value)[:120]
    for name, config in configs.items():
        clock_values(config, name)
    return dict(su=configs[V310_CONFIG]["simulator_config"]["su"]["component"],
                lsu_level=level, clos_router_in_fm_flag=True, log_level=args.log_level,
                clock_fields=clock_fields)


def read_output(case, rank, output_name, address, size):
    paths = [case / "bin" / f"david_{rank}_die{die}_{output_name}.bin" for die in (1, 2)]
    return extract_die_output(paths, output_name, address - rank * rt.GM_BYTES, size)


def validate_send(case, workload, prepared):
    snapshot = json.loads((case / "environment.snapshot.json").read_text())
    library = Path(snapshot["settings"]["library_dir"]) / "libDavinciSimulator.so"
    if checksum(library) != V11_DIE_DECODER_SHA256:
        raise ValueError("V1.1.0 dump has no verified Die decoder")
    retained = {}
    for rank, (_, expected, _) in enumerate(prepared):
        outputs = workload["workload_config"][f"chip{rank}"]["output_param"]
        workspace = outputs["workspace"]
        status = outputs["status"]
        raw = read_output(case, rank, "status", status["addr"], status["size"])
        if any(raw):
            raise ValueError(f"rank {rank} nonzero send kernel status: {struct.unpack_from('<8I', raw)}")
        actual = read_output(case, rank, "workspace", workspace["addr"], workspace["size"])
        if actual != expected:
            offset = next(i for i, (a, b) in enumerate(zip(actual, expected)) if a != b)
            raise ValueError(f"rank {rank} send workspace mismatch at byte {offset}")
        path = case / f"rank{rank}.verified-workspace.bin"
        path.write_bytes(actual)
        retained[rank] = path
    return retained



def transfer_statistics(specs):
    ranks = []
    for spec in specs:
        peers = []
        for gateway, stages in enumerate(spec.backward):
            rows = sum(map(len, stages))
            signals = sum(bool(stage) for stage in stages)
            peers.append(dict(peer=spec.rank // 8 * 8 + gateway, payload_rows=rows,
                              payload_bytes=rows * spec.hidden * 2, so_payload_bytes=signals * 8,
                              wqe_bytes=(rows + signals) * 64))
        ranks.append(dict(rank=spec.rank, payload_bytes=sum(p["payload_bytes"] for p in peers),
                          so_payload_bytes=sum(p["so_payload_bytes"] for p in peers),
                          wqe_bytes=sum(p["wqe_bytes"] for p in peers), peers=peers))
    return dict(per_rank=ranks, payload_bytes_min=min(r["payload_bytes"] for r in ranks),
                payload_bytes_max=max(r["payload_bytes"] for r in ranks),
                payload_bytes_total=sum(r["payload_bytes"] for r in ranks))


def validate_reduce(case, work, specs, send_state):
    for spec in specs:
        output = work["workload_config"][f"chip{spec.rank}"]["output_param"]["workspace"]
        raw = read_output(case, spec.rank, "workspace", output["addr"], output["size"])
        before = send_state[spec.rank].read_bytes()
        expected_controls = bytearray(before[:spec.layout()[6]])
        for reducer in range(spec.reducers):
            slot = 3 * fixtures.SOURCES + 96 + spec.chunks * 8 + reducer
            struct.pack_into("<I", expected_controls, slot * fixtures.SLOT_BYTES, spec.chunks)
        assert raw[:spec.layout()[6]] == expected_controls, "reduce_progress_or_control_guard_rank_" + str(spec.rank)
        assert raw[spec.layout()[6]:spec.layout()[7]] == before[spec.layout()[6]:spec.layout()[7]], "reduce_changed_gather"
        allowed = sorted(spec.partials)
        cursor = spec.layout()[7]
        for row in allowed:
            start = spec.layout()[7] + row * spec.hidden * 2
            assert raw[cursor:start] == before[cursor:start], "reduce_partial_guard_rank_" + str(spec.rank)
            cursor = start + spec.hidden * 2
        assert raw[cursor:] == before[cursor:], "reduce_partial_tail_guard_rank_" + str(spec.rank)


def collect_profiles(case, work, specs):
    ranks = []
    for spec in specs:
        output = work["workload_config"][f"chip{spec.rank}"]["output_param"]["profiling"]
        raw = read_output(case, spec.rank, "profiling", output["addr"], output["size"])
        rank = profile_dump.summarize(raw, profile_dump.capacity(spec), spec.rank, case)
        write_json(case / f"rank{spec.rank}.profiling-summary.json", rank)
        ranks.append(rank)
    stages = {}
    for rank in ranks:
        for name, row in rank["stages"].items():
            total = stages.setdefault(name, dict(count=0, min_ticks=row["min_ticks"], max_ticks=0, sum_lane_ticks=0))
            total["count"] += row["count"]
            total["min_ticks"] = min(total["min_ticks"], row["min_ticks"])
            total["max_ticks"] = max(total["max_ticks"], row["max_ticks"])
            total["sum_lane_ticks"] += row["sum_lane_ticks"]
    return dict(committed_lanes=len(ranks) * 64, dropped_records=0,
                clock_basis="GetSystemCycle raw ticks; conversion not yet confirmed for this model",
                ranks=[{key: rank[key] for key in ("rank", "start_tick", "end_tick", "elapsed_ticks")} for rank in ranks],
                stages=stages)


def run_phase(case, mode, specs, prepared, config, args, send_state=None):
    stage_started = time.monotonic()
    stage_utc = datetime.now(timezone.utc).isoformat()
    work = create_workload(case, specs, prepared, mode, send_state)
    with (case / "model.log").open("w") as log, redirect_stdout(log):
        model = write_model(case, config, work, len(specs))
    write_json(case / "workload.json", work)
    env = config["settings"]
    command = shell_command(env, "sst", [env["sst_bin"], "-n", str(args["threads"]), case / "test_UBusSim.py"])
    started = time.monotonic()
    started_utc = datetime.now(timezone.utc).isoformat()
    cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    with (case / "sim.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=case, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        write_json(WORK / "progress.json", dict(state="simulating", mode=args["mode"], case=str(case),
                                                pid=process.pid, started_utc=started_utc))
        timed_out = False
        try:
            code = process.wait(timeout=args["stage_timeout"])
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=15)
            code = 124
    cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    result = dict(ok=False, mode=args["mode"], report=str(case), returncode=code, timed_out=timed_out,
                  stage_started_utc=stage_utc, sst_started_utc=started_utc,
                  sst_finished_utc=datetime.now(timezone.utc).isoformat(), sst_wall_s=time.monotonic() - started,
                  sst_cpu_user_s=cpu_after.ru_utime - cpu_before.ru_utime,
                  sst_cpu_system_s=cpu_after.ru_stime - cpu_before.ru_stime, model=model)
    finish, fatal = [], False
    with (case / "sim.log").open("rb") as log:
        log.seek(max(0, (case / "sim.log").stat().st_size - 2**20))
        for line in log.read().decode(errors="replace").splitlines():
            match = SIM_TIME.search(line)
            if match:
                finish.append(float(match[1]) * TIME_FACTORS[match[2]])
            fatal |= "FATAL:" in line or "Traceback (most recent call last)" in line
    result["completed"] = code == 0 and not fatal and bool(finish) and finish[-1] > 0
    if finish:
        result["simulated_us"] = finish[-1]
    failures = []
    if result["completed"]:
        try:
            if mode == 1:
                retained = validate_send(case, work, prepared)
                result.update(verified_ranks=len(specs), numeric_validation="PASS",
                              verified_workspace_sha256={str(rank): checksum(path) for rank, path in retained.items()})
            else:
                validate_reduce(case, work, specs, send_state)
                result.update(verified_ranks=len(specs), numeric_validation="SKIPPED_KNOWN_FP32_TO_BF16_CAST",
                              input_origin="verified_send_dump", progress_and_guards="PASS")
        except (OSError, ValueError, AssertionError) as error:
            failures.append("output_validation: " + str(error)[:500])
        try:
            result["profiling"] = collect_profiles(case, work, specs)
        except (OSError, ValueError, AssertionError) as error:
            failures.append("profiling_validation: " + str(error)[:500])
    else:
        failures.append("sst_timeout" if timed_out else "sst_did_not_complete")
    result["failures"] = failures
    result["ok"] = not failures
    result["stage_wall_s"] = time.monotonic() - stage_started
    write_json(case / "run.json", result)
    write_json(WORK / "progress.json", dict(state="complete" if result["ok"] else "failed", result=result))
    return result


def run(args):
    ranks, tokens = args["ranks"], args["tokens"]
    assert ranks in (8, 16, 32, 64) and type(tokens) is int and tokens >= 4
    assert args["hidden"] == 256 and args["mode"] in ("send", "reduce")
    assert type(args["threads"]) is int and 1 <= args["threads"] <= 64
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args["case_id"])
    assert checksum(KERNEL) == args["kernel_sha256"], "kernel_identity_changed"
    config = json.loads((WORK / "environment.snapshot.json").read_text())
    verify_environment(config)
    specs, routes = fixtures.prepare(ranks, tokens, 256, 16, min(tokens, 4), 40, 64)
    route_bytes = json.dumps(routes, separators=(",", ":")).encode()
    case = WORK / "cases" / args["case_id"]
    case.mkdir(parents=True, exist_ok=True)
    record = dict(ranks=ranks, source_ranks=64, bs=tokens, topk=16, hidden=256, reducers=40,
                  chunk=16, splits=min(tokens, 4), route_seed=64, route_sha256=hashlib.sha256(route_bytes).hexdigest(),
                  kernel_sha256=checksum(KERNEL), report=str(case), sst_threads=args["threads"],
                  source_revision=json.loads((WORK / "revisions.json").read_text())["candidate"],
                  transfers=transfer_statistics(specs), profile_capacities=[profile_dump.capacity(s) for s in specs])
    manifest = case / "case.json"
    if manifest.exists():
        assert json.loads(manifest.read_text()) == record, "case_configuration_changed"
    else:
        assert args["mode"] == "send", "case_send_not_run"
        write_json(manifest, record)
        (case / "topk64p.json").write_bytes(route_bytes)
    phase = case / (args["mode"] + "-only")
    assert not phase.exists(), "phase_already_attempted_do_not_replay"
    send_state = None
    if args["mode"] == "reduce":
        send = json.loads((case / "send-only/run.json").read_text())
        assert send["ok"] and send["verified_ranks"] == ranks, "send_not_verified"
        send_state = {s.rank: case / "send-only" / f"rank{s.rank}.verified-workspace.bin" for s in specs}
        assert all(checksum(path) == send["verified_workspace_sha256"][str(rank)] for rank, path in send_state.items())
    estimate = sum(s.layout()[8] * 10 + 8 * s.backward_capacity * 24 * 2 +
                   profile_dump.layout(profile_dump.capacity(s))["bytes"] * 8 for s in specs)
    assert shutil.disk_usage(case).free > estimate + 512 * 1024**2, "insufficient_disk_budget"
    prepared = [spec.serialize() for spec in specs]
    write_json(WORK / "progress.json", dict(state="preparing", case=str(case), mode=args["mode"]))
    result = run_phase(phase, 1 if args["mode"] == "send" else 2, specs, prepared, config, args, send_state)
    result.update(ranks=ranks, source_ranks=64, bs=tokens, topk=16, hidden=256,
                  kernel_sha256=record["kernel_sha256"], source_revision=record["source_revision"],
                  transfers={key: value for key, value in record["transfers"].items() if key != "per_rank"})
    write_json(case / (args["mode"] + "-result.json"), result)
    return result


def main(args):
    try:
        result = setup() if args["stage"] == "setup" else run(args)
    except (OSError, ValueError, KeyError, AssertionError, subprocess.SubprocessError) as error:
        result = dict(ok=False, stage=args["stage"], error_type=type(error).__name__, detail=str(error)[:700])
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
