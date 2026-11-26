from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import base64
import csv
import hashlib
import importlib.util
import json
import runpy
import statistics
import sys
import time
import uuid

from controller import OUTPUT, REMOTE, RUNNER, STATE, WINDOWS, HISTORICAL
from key_transport import install, make_profile

RESULT = STATE / "ubx-topk-perf-20261126-results"
KERNEL = "lib/tilexr/kernels/tilexr_ep_ubx_kernel.o"
STAGES = {
    "run": (1, 2),
    "topk_exchange": (240, 241),
    "control_ready": (256, 257),
    "expert_exchange_wait": (268, 269),
    "expert_compute": (270, 271),
    "expert_ready_wait": (286, 287),
    "recv_index": (45, 46),
    "recv_progress": (49, 50),
    "run_to_first_copyout": (1, 58),
}


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def stats(values):
    return [round(f(values), 4) for f in (min, statistics.median, max)]


def route_profile(profile, generator, outcome, batch, mode, package, artifact_hashes, cann):
    profile = deepcopy(profile)
    case = profile["case"]
    routes = RESULT / f"routes-bs{batch}.bin"
    if not routes.exists():
        generator(32, batch, 12, 8, 261011, routes)
    raw = routes.read_bytes()
    case.update(batch=batch, seed=261011, routes=str(routes.resolve()), route_sha256=hashlib.sha256(raw).hexdigest(),
                role_cores=[16, 16, 16, 8], rounds=25, warmup_rounds=5, native_msprof=True,
                ordinary_profiling=mode == "on", route_mode="local", rotate_routes=False)
    runtime = profile["runtime"]
    runtime.update(prefix=f"{REMOTE}/{package}", abi=18, sha256=artifact_hashes)
    if Path(runtime["cann_env"]).parent.name != cann and cann == "cann-9.3.0":
        runtime["cann_env"] = "/usr/local/Ascend/cann-9.3.0/set_env.sh"
        runtime["msprof"] = "/usr/local/Ascend/cann-9.3.0/tools/profiler/bin/msprof"
    profile.update(workspace=f"{REMOTE}/runs", validation_mode="performance")
    write_json(outcome / "profile.private.json", profile)
    return profile, raw


def worker_source():
    source = RUNNER.with_name("ubx_case_worker.py").read_text(encoding="utf-8")
    replacements = [
        ("runtime_abi in (16, 17)", "runtime_abi in (16, 17, 18)"),
        ("runtime_abi == 17 or (route_mode", "runtime_abi in (17, 18) or (route_mode"),
        ("data = base64.b64decode(request['candidate']['files'][KERNEL_FILE], validate=True)",
         "data = (source / KERNEL_FILE).read_bytes()"),
        ("        TILEXR_UBX_DOORBELL_BATCH=64,",
         "        TILEXR_UBX_DOORBELL_BATCH=64,\n        TILEXR_UBX_MAX_TOKENS=2,"),
        ("'Summary contract mismatch')", "'Summary contract mismatch')\n"
         "            require(item.get('max_tokens') == 2 and item.get('packet_trace') is False, "
         "'Packet limit or capture mode mismatch')"),
    ]
    for from_, to in replacements:
        if source.count(from_) != 1:
            raise ValueError("retained worker shape changed: " + from_)
        source = source.replace(from_, to)
    compile(source, "ubx_case_worker_abi18.py", "exec")
    return source


def analyze(output, profile, record_reader):
    complete = defaultdict(dict)
    stages = []
    traffic = []
    starts = defaultdict(set)
    for rank in range(32):
        rankdir = output / "raw" / f"node{rank // 16}" / f"rank-{rank}"
        files = list((rankdir / "msprof").rglob("op_summary_*.csv"))
        assert len(files) == 1
        with files[0].open(encoding="utf-8-sig", newline="") as stream:
            watched = [row for row in csv.DictReader(stream) if row["Op Name"] == "tilexr_ep_ubx_kernel"]
        assert len(watched) == 25
        watched.sort(key=lambda row: Decimal(row["Task Start Time(us)"]))
        assert len({row["Task Start Time(us)"] for row in watched}) == 25
        for round_id, row in enumerate(watched):
            complete[round_id][rank] = float(row["Task Duration(us)"])
            stem = rankdir / f"rank-{rank}-round-{round_id}"
            clock = json.loads(stem.with_suffix(".clock.json").read_text(encoding="utf-8"))
            starts[round_id].add((clock["target"], clock["sleep_enabled"]))
            if round_id >= 5:
                summary = json.loads(stem.with_suffix(".summary.json").read_text(encoding="utf-8"))
                traffic.append([summary["rdma_data_bytes"], summary["data_wqes"],
                                summary["intra_wire_bytes"], summary["output_rows"]])
            if not profile["case"]["ordinary_profiling"]:
                continue
            records = record_reader(stem.with_suffix(".records.bin").read_bytes())
            assert len(records.streams) == 112 and all(
                lane.complete and not lane.overflow and lane.dropped_count == 0 for lane in records.streams)
            physical = [lane for lane in records.streams if (lane.id & 0xFFFF) < 56]
            assert {lane.id for lane in physical} == {(rank << 16) + core for core in range(56)}
            for lane in physical:
                events = {record.event_id: record.timestamp for record in lane.records}
                assert 1 in events and 2 in events
                core = lane.id & 0xFFFF
                if not 16 <= core < 32:
                    continue
                for name, (begin, end) in STAGES.items():
                    if begin in events and end in events:
                        assert events[begin] <= events[end]
                        stages.append((rank, round_id, core, name,
                                       (events[end] - events[begin]) * 1e6 / clock["cycles_per_second"]))
    assert all(len(values) == 1 and next(iter(values))[1] for values in starts.values())
    with (output / "native-samples.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["rank", "round", "warmup", "dispatch_us"])
        writer.writerows((rank, round_id, round_id < 5, complete[round_id][rank])
                         for round_id in sorted(complete) for rank in range(32))
    with (output / "recv-stage-samples.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["rank", "round", "core", "stage", "duration_us"])
        writer.writerows(stages)
    by_rank = [statistics.median(complete[round_id][rank] for round_id in range(5, 25)) for rank in range(32)]
    result = {
        "rank_rounds": 800,
        "rank_medians_min_median_max_us": stats(by_rank),
        "slowest_rank_each_round_min_median_max_us": stats([max(complete[round_id].values())
                                                            for round_id in range(5, 25)]),
        "rdma_data_bytes_by_rank_round": stats([row[0] for row in traffic]),
        "rdma_data_wqes_by_rank_round": stats([row[1] for row in traffic]),
        "intra_wire_bytes_by_rank_round": stats([row[2] for row in traffic]),
        "recv_stage_median_us": {stage: round(statistics.median(row[4] for row in stages
            if row[3] == stage and row[1] >= 5), 4) for stage in STAGES
            if any(row[3] == stage and row[1] >= 5 for row in stages)},
    }
    write_json(output / "summary.json", result)
    return result


def main(batch):
    build = json.loads((OUTPUT / "result.private.json").read_text())
    assert len(build) == 2 and all(row["status"] == "PASS" for row in build), "build incomplete"
    assert build[0]["cann"] == build[1]["cann"]
    RESULT.mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location("ep32_runner", RUNNER)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    helper = HISTORICAL / "ubx-task-rsa-key-transport.py"
    if helper.is_file():
        assert hashlib.sha256(helper.read_bytes()).hexdigest() == "847b4a4801b6ddc63315aacbb092f4c0336241cb276bd96c1f954284276d129a"
        runpy.run_path(str(helper))["install"](runner)
    source = OUTPUT / "source.git"
    generator = runpy.run_path(str(source / "tools/ep/generate_ubx_routes.py"))["generate"]
    sys.path.insert(0, str(source / "tools/profiling/python"))
    from tilexr_profiling.records import parse_record_bytes

    template = make_profile(runner, WINDOWS / "ep32-local.json", OUTPUT)
    runner.transport = install(runner, template, runner.transport if helper.is_file() else None)
    worker = worker_source()
    checks = RESULT / "occupancy" / uuid.uuid4().hex[:12]
    checks.mkdir(parents=True)
    runtime = deepcopy(template)
    runtime["runtime"].update(prefix=f"{REMOTE}/baseline-off")
    if Path(runtime["runtime"]["cann_env"]).parent.name != build[0]["cann"] and build[0]["cann"] == "cann-9.3.0":
        runtime["runtime"]["cann_env"] = "/usr/local/Ascend/cann-9.3.0/set_env.sh"
    mismatch = [label for label in build[0]["artifacts"]
                if build[0]["artifacts"][label] != build[1]["artifacts"].get(label)]
    if mismatch:
        print(json.dumps({"status": "BLOCKED", "reason": "build_package_hash_mismatch",
            "packages": mismatch, "different_files": {label: [path for path, digest in
                build[0]["artifacts"][label].items() if digest != build[1]["artifacts"].get(label, {}).get(path)]
                for label in mismatch}}, separators=(",", ":")))
        return False

    def wait_idle(batch, package):
        deadline = time.monotonic() + 120
        poll = 0
        while True:
            poll += 1

            def probe(node):
                command = (f'import json\nfrom pathlib import Path\nns={{"__name__":"ubx_worker"}}\n'
                           f'exec(compile({worker!r},"worker18.py","exec"),ns)\n'
                           f'p={runtime!r}\n'
                           f'env=ns["runtime_environment"](p,Path({runtime["runtime"]["prefix"]!r}),"lib")\n'
                           f'rows=ns["probe_devices"](p,{node},env)\n'
                           f'print(json.dumps({{"status":"PASS","rows":rows}}))\n')
                return runner.transport(runtime, node, command, checks,
                                        f"bs{batch}-{package}-{poll}", 50)

            with ThreadPoolExecutor(max_workers=2) as pool:
                values = list(pool.map(probe, range(2)))
            brief = [{"node": node, "error": row.get("reason"), "status": row.get("status"),
                      "busy": [d["device"] for d in row.get("rows", []) if d.get("pids")],
                      "indeterminate": [d["device"] for d in row.get("rows", []) if
                          not d.get("usable") and not d.get("pids")],
                      "usable": sum(bool(d.get("usable")) for d in row.get("rows", []))}
                     for node, row in enumerate(values)]
            if all(row["usable"] == 16 for row in brief):
                return True
            if time.monotonic() >= deadline or any(
                row["status"] != "PASS" or row["indeterminate"] for row in brief
            ):
                print(json.dumps({"status": "BLOCKED", "bs": batch, "package": package,
                                  "reason": "device_preflight", "nodes": brief}, separators=(",", ":")))
                return False
            time.sleep(5)

    # Run a batch as one transaction with four separately archived configurations.
    assert batch in (32, 64, 128)
    summaries = []
    for mode in ("off", "on"):
        for variant in ("baseline", "candidate"):
            package = variant + "-" + mode
            if not wait_idle(batch, package):
                return False
            outcome = RESULT / f"bs{batch}-{package}"
            outcome.mkdir(exist_ok=False)
            if not helper.is_file():
                (outcome / "ssh-askpass.sh").write_bytes((OUTPUT / "ssh-askpass.sh").read_bytes())
                (outcome / "ssh-askpass.sh").chmod(0o700)
            hashes = build[0]["artifacts"][package]
            assert hashes == build[1]["artifacts"][package], "host package hash mismatch"
            profile, routes = route_profile(template, generator, outcome, batch, mode, package,
                                             {k: v for k, v in hashes.items() if k != KERNEL}, build[0]["cann"])
            run_id = "topk-bs" + str(batch) + "-" + package + "-" + uuid.uuid4().hex[:10]
            capture = uuid.uuid4().hex
            write_json(outcome / "run-identity.json", {"run_id": run_id, "capture": capture})

            def phase(action):
                def run(node):
                    candidate = {"version": 1, "abi": 18, "mode": "kernel_only",
                                 "ordinary_profiling": mode == "on", "sha256": {KERNEL: hashes[KERNEL]}}
                    request = dict(phase=action, run_id=run_id, capture=capture, node=node, profile=profile,
                                   port=profile["network"]["ports"][0], candidate=candidate)
                    if action == "prepare":
                        candidate["files"] = {KERNEL: "on_host"}
                        request["routes_b64"] = base64.b64encode(routes).decode("ascii")
                    command = (f'import json\nns={{"__name__":"ubx_worker"}}\nexec(compile({worker!r},'
                               f'"worker18.py","exec"),ns)\nprint(json.dumps(ns["run"]({request!r})))\n')
                    return runner.transport(profile, node, command, outcome, action, 900 if action == "run" else 180)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    result = list(pool.map(run, range(2)))
                write_json(outcome / (action + ".private.json"), result)
                return result

            for step in ("prepare", "run", "collect"):
                hosts = phase(step)
                if not all(row.get("status") == "PASS" and
                           (step != "run" or row.get("performance_valid")) for row in hosts):
                    print(json.dumps({"status": "BLOCKED", "step": step, "bs": batch, "package": package,
                        "run_id": run_id, "remote_root": profile["workspace"], "windows_output": str(outcome),
                        "hosts": [{k: row[k] for k in ("status", "reason", "hardware_launched", "performance_valid")
                                   if k in row} for row in hosts]}, ensure_ascii=True))
                    return False
            archives = [runner.collect_archive(profile, node, row, outcome, run_id) for node, row in enumerate(hosts)]
            data = analyze(outcome, profile, parse_record_bytes)
            summaries.append({"package": package, "run_id": run_id,
                              "archives": [{"bytes": v["bytes"], "sha256": v["sha256"]} for v in archives], **data})
    write_json(RESULT / f"bs{batch}-report.json", summaries)
    print(json.dumps({"status": "PASS", "bs": batch, "windows_output": str(RESULT), "compared": summaries},
                     ensure_ascii=True, separators=(",", ":")))
    return True


if __name__ == "__main__":
    try:
        assert sys.argv[1] == "--args-file"
        arguments = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        batches = arguments.get("batches")
        if batches is None:
            batches = [arguments["batch"]]
        assert sorted(set(batches)) == sorted(batches) and set(batches) <= {32, 64, 128}
        ok = all(main(int(batch)) for batch in batches)
    except Exception as error:
        print(json.dumps({"status": "FAILED", "error_type": type(error).__name__, "output": str(RESULT)},
                         ensure_ascii=True))
        ok = False
    raise SystemExit(0 if ok else 1)
