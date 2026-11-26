from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from decimal import Decimal
from pathlib import Path, PurePosixPath
import base64
import csv
import hashlib
import importlib.util
import json
import runpy
import shutil
import statistics
import sys
import tarfile
import time
import traceback
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


def route_profile(profile, generator, outcome, batch, mode, package, artifact_hashes, cann, attempt):
    profile = deepcopy(profile)
    case = profile["case"]
    batch_offset = (32, 64, 128).index(batch) * 4
    package_offset = ("baseline-off", "candidate-off", "baseline-on", "candidate-on").index(package)
    port = profile["network"]["ports"][0] + 4 * (batch_offset + package_offset + 12 * attempt)
    assert 1024 <= port <= 65532
    profile["network"]["ports"] = [port]
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
    launch_receipt = """    receipt = directory / 'started.json'
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump({'run_id': request['run_id'], 'started_at': time.time()}, stream)
"""
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
        ("    if not report['idle']:\n        return report\n    check_ports(request['port'])\n", ""),
        (launch_receipt, ""),
        ("    check_ports(request['port'])\n    children, streams",
         "    check_ports(request['port'])\n" + launch_receipt + "    children, streams"),
        ("        if not result_path.exists():",
         "        busy_before_launch = request['phase'] == 'run' and retryable_occupancy(result)\n"
         "        if not result_path.exists() and not busy_before_launch:"),
        ("def run(request):",
         "def retryable_occupancy(result):\n"
         "    return (result.get('status') == 'BLOCKED' and result.get('hardware_launched') is False\n"
         "            and any(row.get('pids') for row in result.get('before_occupancy', ())))\n\n\n"
         "def run(request):"),
        ("        while any(p.poll() is None for _, p in children):",
         "        while any(p.poll() is None for _, p in children):\n"
         "            require(not (directory / 'peer-failed.flag').exists(), 'Peer benchmark worker failed')"),
    ]
    for from_, to in replacements:
        if source.count(from_) != 1:
            raise ValueError("retained worker shape changed: " + from_)
        source = source.replace(from_, to)
    bare_interference = "                    require(group in groups, 'Foreign workload detected; performance invalid')"
    detailed_interference = """                    if group not in groups:
                        try:
                            executable = (Path('/proc') / str(pid) / 'exe').resolve(strict=True)
                            kind = ('current_fixture' if executable == package / 'bin/ubx_full_intra' else
                                    'other_fixture' if executable.name == 'ubx_full_intra' else
                                    'profiler' if executable.name.startswith('msprof') else
                                    'python' if executable.name.startswith('python') else
                                    'shell' if executable.name in ('sh', 'bash', 'zsh') else 'other')
                        except OSError:
                            kind = 'unavailable'
                        report['interference'] = {'device': row['device'], 'kind': kind}
                        try:
                            report['interference']['own_session'] = os.getsid(pid) in groups
                            parent = pid
                            for _ in range(16):
                                proc = (Path('/proc') / str(parent) / 'stat').read_text()
                                parent = int(proc.rsplit(')', 1)[1].split()[1])
                                if parent in groups or parent <= 1:
                                    break
                            report['interference']['own_parent'] = parent in groups
                        except (OSError, ValueError, IndexError):
                            pass
                        save(directory / 'unexpected-process.json', report['interference'])
                        raise ValueError('Foreign workload detected; performance invalid')"""
    source = source.replace(bare_interference, detailed_interference)
    compile(source, "ubx_case_worker_abi18.py", "exec")
    return source


def retryable_occupancy(result):
    return (result.get("status") == "BLOCKED" and result.get("hardware_launched") is False
            and any(row.get("pids") for row in result.get("before_occupancy", ())))


def blocked_before_launch(result):
    return retryable_occupancy(result) or (
        result.get("status") == "BLOCKED" and result.get("hardware_launched") is False
        and result.get("reason") == "ports_busy"
    )


def interrupted_by_foreign_workload(hosts):
    if len(hosts) != 2 or not any(
        "Foreign workload detected" in host.get("error", "") or retryable_occupancy(host) for host in hosts
    ):
        return False
    return all(retryable_occupancy(host) or (
        host.get("hardware_launched") is True and host.get("network_restored") is True
        and host.get("termination_confirmed") is not False and not host.get("cleanup_error")
        and len(host.get("exit_codes", {})) == 16
        and all(isinstance(code, int) for code in host["exit_codes"].values())
    ) for host in hosts)


def read_receipts(outcome, phase):
    path = outcome / f"{phase}.private.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def extract_capture(archive, destination):
    destination.mkdir(parents=True)
    root = destination.resolve()
    paths = {}
    with tarfile.open(archive) as source:
        for member in source:
            relative = PurePosixPath(member.name)
            if not member.isfile() or relative.is_absolute() or ".." in relative.parts or "\\" in member.name:
                raise ValueError("Unsafe capture archive member")
            if len(relative.parts) >= 3 and relative.parts[1] == "msprof" and relative.suffix == ".csv":
                relative = PurePosixPath(relative.parts[0], "msprof", relative.name)
            target = (root / relative.as_posix()).resolve()
            if not target.is_relative_to(root) or target == root / "archive-paths.json":
                raise ValueError("Unsafe capture archive path")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as data, target.open("xb") as sink:
                shutil.copyfileobj(data, sink)
            paths[member.name] = relative.as_posix()
    write_json(root / "archive-paths.json", paths)


def archive_capture(runner, profile, node, receipt, outcome, run_id):
    remote = profile["workspace"].rstrip("/") + "/" + run_id + "/captures.tar.gz"
    if receipt["capture_archive_path"] != remote:
        raise ValueError("Capture archive is outside this run")
    size, digest = receipt["capture_archive_bytes"], receipt["capture_archive_sha256"]
    archive = outcome / f"node-{node}-captures.tar.gz"
    if not archive.is_file() or runner.digest(archive) != digest:
        partial = archive.with_suffix(".partial")
        with partial.open("wb") as stream:
            for offset in range(0, size, 1024 * 1024):
                length = min(1024 * 1024, size - offset)
                script = ('import base64,json\n'
                          f'with open({remote!r}, "rb") as stream:\n'
                          f' stream.seek({offset})\n data=stream.read({length})\n'
                          'print(json.dumps({"status":"PASS","data":base64.b64encode(data).decode("ascii")}))\n')
                label = f"capture-{offset}"
                chunk = runner.transport(profile, node, script, outcome, label, 60)
                if chunk.get("status") != "PASS":
                    raise ValueError("Capture transfer failed")
                data = base64.b64decode(chunk["data"], validate=True)
                if len(data) != length:
                    raise ValueError("Capture transfer returned an incomplete chunk")
                stream.write(data)
                (outcome / f"{label}-{node}.stdout").unlink(missing_ok=True)
        if runner.digest(partial) != digest:
            raise ValueError("Capture archive hash mismatch")
        partial.replace(archive)
    destination = outcome / "raw" / f"node{node}"
    certificate = destination / "archive-paths.json"
    if not certificate.is_file():
        if destination.exists():
            destination.rename(outcome / f"interrupted-node-{node}-{uuid.uuid4().hex[:8]}")
        extract_capture(archive, destination)
    return {"bytes": size, "sha256": digest}


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


def main(batch, probe_only=False, wait_seconds=120, min_idle_seconds=0):
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

    def update_status(package, state, **details):
        staged = checks / "status.tmp"
        write_json(staged, dict(batch=batch, package=package, state=state,
                                updated_at=time.time(), **details))
        staged.replace(checks / "status.json")

    def wait_idle(batch, package, deadline):
        poll = 0
        idle_since = None
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
                                        f"bs{batch}-{package}-{poll}-{time.time_ns()}", 50)

            with ThreadPoolExecutor(max_workers=2) as pool:
                values = list(pool.map(probe, range(2)))
            brief = [{"node": node, "error": row.get("reason"), "status": row.get("status"),
                      "busy": [d["device"] for d in row.get("rows", []) if d.get("pids")],
                      "indeterminate": [d["device"] for d in row.get("rows", []) if
                          not d.get("usable") and not d.get("pids")],
                      "usable": sum(bool(d.get("usable")) for d in row.get("rows", []))}
                     for node, row in enumerate(values)]
            now = time.monotonic()
            idle = all(row["usable"] == 16 for row in brief)
            if idle:
                idle_since = now if idle_since is None else idle_since
            else:
                idle_since = None
            consecutive_idle = 0 if idle_since is None else now - idle_since
            update_status(package, "waiting_for_devices", nodes=brief, polls=poll,
                          consecutive_idle_seconds=round(consecutive_idle, 1))
            if probe_only:
                print(json.dumps({"status": "READY" if all(row["usable"] == 16 for row in brief)
                                  else "BLOCKED", "nodes": brief}, separators=(",", ":")))
                return True
            if idle and consecutive_idle >= min_idle_seconds:
                return True
            if time.monotonic() >= deadline or any(
                row["status"] != "PASS" or row["indeterminate"] for row in brief
            ):
                print(json.dumps({"status": "BLOCKED", "bs": batch, "package": package,
                                  "reason": "device_preflight", "nodes": brief}, separators=(",", ":")))
                return False
            time.sleep(min(15, max(0, deadline - time.monotonic())))

    # Run a batch as one transaction with four separately archived configurations.
    assert batch in (32, 64, 128)
    if probe_only:
        return wait_idle(batch, "readiness", time.monotonic())
    summaries = []
    for mode in ("off", "on"):
        for variant in ("baseline", "candidate"):
            package = variant + "-" + mode
            outcome = RESULT / f"bs{batch}-{package}"
            previous = outcome / "summary.json"
            if previous.is_file():
                raw = json.loads(previous.read_text(encoding="utf-8"))
                identity = json.loads((outcome / "run-identity.json").read_text(encoding="utf-8"))
                archives = json.loads((outcome / "collect.private.json").read_text(encoding="utf-8"))
                assert raw["rank_rounds"] == 800 and len(archives) == 2
                assert all((outcome / f"node-{node}-captures.tar.gz").is_file() for node in range(2))
                summaries.append({"package": package, "run_id": identity["run_id"],
                    "archives": [{"bytes": v["capture_archive_bytes"], "sha256": v["capture_archive_sha256"]}
                                 for v in archives], **raw})
                continue
            hashes = build[0]["artifacts"][package]
            assert hashes == build[1]["artifacts"][package], "host package hash mismatch"
            prior_run = []
            hosts = []
            if outcome.exists():
                prior_run = read_receipts(outcome, "run")
                prepared = read_receipts(outcome, "prepare")
                if (outcome / "run.in-flight").exists():
                    raise RuntimeError("Case launch outcome is unknown: " + package)
                if len(prior_run) == 2 and (all(blocked_before_launch(host) for host in prior_run)
                                            or interrupted_by_foreign_workload(prior_run)):
                    archived = RESULT / "previous-attempts"
                    archived.mkdir(exist_ok=True)
                    outcome.rename(archived / f"{outcome.name}-{time.time_ns()}")
                elif prior_run and (len(prior_run) != 2 or any(
                    host.get("status") != "PASS" or not host.get("performance_valid") for host in prior_run
                )):
                    raise RuntimeError("Case already has an unfinished attempt: " + package)
                elif len(prepared) != 2 or any(host.get("status") != "PASS" for host in prepared):
                    raise RuntimeError("Preparation outcome is unknown: " + package)
            if outcome.exists():
                profile = json.loads((outcome / "profile.private.json").read_text(encoding="utf-8"))
                identity = json.loads((outcome / "run-identity.json").read_text(encoding="utf-8"))
                run_id, capture = identity["run_id"], identity["capture"]
                steps = ("run", "collect")
                if prior_run:
                    hosts = read_receipts(outcome, "collect")
                    steps = () if len(hosts) == 2 and all(host.get("status") == "PASS" for host in hosts) else ("collect",)
            else:
                outcome.mkdir()
                if not helper.is_file():
                    (outcome / "ssh-askpass.sh").write_bytes((OUTPUT / "ssh-askpass.sh").read_bytes())
                    (outcome / "ssh-askpass.sh").chmod(0o700)
                attempt = len(list((RESULT / "previous-attempts").glob(outcome.name + "-*")))
                profile, routes = route_profile(template, generator, outcome, batch, mode, package,
                                                 {k: v for k, v in hashes.items() if k != KERNEL},
                                                 build[0]["cann"], attempt)
                run_id = "topk-bs" + str(batch) + "-" + package + "-" + uuid.uuid4().hex[:10]
                capture = uuid.uuid4().hex
                write_json(outcome / "run-identity.json", {"run_id": run_id, "capture": capture})
                steps = ("prepare", "run", "collect")

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

                def stop_peer(node):
                    target = f"{profile['workspace']}/{run_id}"
                    script = (f"import json\nfrom pathlib import Path\nroot=Path({target!r})\n"
                              "live = not (root / 'run-result.json').exists()\n"
                              "if live: (root / 'peer-failed.flag').touch()\n"
                              "print(json.dumps({'status':'PASS'}))\n")
                    return runner.transport(profile, node, script, outcome, "peer-failed", 25)

                with ThreadPoolExecutor(max_workers=2) as pool:
                    pending = {pool.submit(run, node): node for node in range(2)}
                    result = [None, None]
                    for future in as_completed(pending):
                        node = pending[future]
                        result[node] = future.result()
                        if (action == "run" and result[1 - node] is None
                                and (retryable_occupancy(result[node]) or (
                                    result[node].get("status") == "FAIL"
                                    and result[node].get("hardware_launched") is True))):
                            stop_peer(1 - node)
                write_json(outcome / (action + ".private.json"), result)
                return result

            deadline = time.monotonic() + wait_seconds
            retries = 0
            for step in steps:
                if step == "run":
                    while True:
                        if not wait_idle(batch, package, deadline):
                            return False
                        update_status(package, step, run_id=run_id)
                        in_flight = outcome / "run.in-flight"
                        in_flight.touch(exist_ok=False)
                        hosts = phase(step)
                        in_flight.unlink()
                        if time.monotonic() >= deadline or not all(retryable_occupancy(host) for host in hosts):
                            break
                        retries += 1
                        write_json(outcome / f"run-preflight-retry-{retries}.private.json", hosts)
                        time.sleep(min(15, max(0, deadline - time.monotonic())))
                else:
                    update_status(package, step, run_id=run_id)
                    hosts = phase(step)
                if not all(row.get("status") == "PASS" and
                           (step != "run" or row.get("performance_valid")) for row in hosts):
                    retry = step == "run" and interrupted_by_foreign_workload(hosts)
                    print(json.dumps({"status": "RETRY" if retry else "BLOCKED", "step": step,
                        "bs": batch, "package": package,
                        "run_id": run_id, "remote_root": profile["workspace"], "windows_output": str(outcome),
                        "hosts": [{k: row[k] for k in ("status", "reason", "hardware_launched", "performance_valid")
                                   if k in row} for row in hosts]}, ensure_ascii=True))
                    return "retry" if retry else False
            update_status(package, "transfer", run_id=run_id)
            with ThreadPoolExecutor(max_workers=2) as pool:
                transfers = [pool.submit(archive_capture, runner, profile, node, row, outcome, run_id)
                             for node, row in enumerate(hosts)]
                archives = [transfer.result() for transfer in transfers]
            update_status(package, "analyze", run_id=run_id)
            data = analyze(outcome, profile, parse_record_bytes)
            summaries.append({"package": package, "run_id": run_id,
                              "archives": [{"bytes": v["bytes"], "sha256": v["sha256"]} for v in archives], **data})
    write_json(RESULT / f"bs{batch}-report.json", summaries)
    update_status("all", "complete")
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
        wait_seconds = int(arguments.get("wait_seconds", 120))
        assert 1 <= wait_seconds <= 3600
        min_idle_seconds = int(arguments.get("min_idle_seconds", 0))
        assert 0 <= min_idle_seconds < wait_seconds
        ok = True
        for batch in batches:
            for attempt in range(4):
                result = main(int(batch), arguments.get("probe_only", False), wait_seconds, min_idle_seconds)
                if result != "retry":
                    break
            if result is not True:
                ok = False
                break
    except Exception as error:
        (RESULT / f"matrix-error-{time.time_ns()}.log").write_text(traceback.format_exc(), encoding="utf-8")
        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(json.dumps({"status": "FAILED", "error_type": type(error).__name__,
                          "function": frame.name, "line": frame.lineno, "output": str(RESULT)},
                         ensure_ascii=True))
        ok = False
    raise SystemExit(0 if ok else 1)
