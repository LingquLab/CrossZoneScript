"""Run a two-node EP32 UBX benchmark queue independently of the bridge."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
from statistics import median
import subprocess
import sys
import time
import uuid

BASE = Path(__file__).resolve().parent
ABC_BRANCHES = ("tail-a", "tail-b", "tail-c")
TAIL_BRANCHES = ("tail-base", "tail-reviewed", *ABC_BRANCHES)
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
       "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no",
       "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
CONFIGS = [("stages", "write", 32), ("dispatch", "dispatch", 64),
           ("dispatch", "dispatch", 128), ("stages", "read", 32),
           ("sleep", "write", 32)]
CONFIGS += [(branch, op, bs) for bs in (64, 128)
            for branch, op in (("stages", "write"), ("stages", "read"), ("sleep", "write"))]
CASES = [(branch, op, bs, variant) for branch, op, bs in CONFIGS
         for variant in ("off", "on")]
matrix_file = BASE / "matrix.json"
if matrix_file.is_file():
    CASES = [tuple(case) for case in json.loads(matrix_file.read_text(encoding="utf-8"))]
ENV = {
    "RANK_SIZE": "32", "TILEXR_ENABLE_IPC": "1", "TILEXR_IPC_SCOPE": "local",
    "TILEXR_ENABLE_RDMA": "1", "TILEXR_ENABLE_UDMA": "0", "TILEXR_ENABLE_SDMA": "0",
    "TILEXR_RDMA_PORT": "1", "TILEXR_RDMA_MODE": "aiv", "TILEXR_RDMA_DMA_MODE": "indep_ub",
    "TILEXR_RDMA_DB_MODE": "hard", "TILEXR_RDMA_GID_INDEX": "1", "TILEXR_RDMA_MTU": "4096",
    "TILEXR_RDMA_TIMEOUT_MS": "30000", "TILEXR_UBX_EXPERTS": "384",
    "TILEXR_UBX_ROUNDS": "25", "TILEXR_UBX_CARDS_PER_SERVER": "16", "TILEXR_UBX_CARDS": "16",
    "TILEXR_UBX_SEND_CORES": "16", "TILEXR_UBX_RECV_CORES": "16",
    "TILEXR_UBX_RELAY_CORES": "16", "TILEXR_UBX_CORES": "56",
    "TILEXR_UBX_PACKET_CAPTURE": "0", "TILEXR_UBX_REUSE": "0",
    "TILEXR_UBX_INTRA_ONLY": "0", "TILEXR_UBX_EVENT_TIMING": "1",
}


def probe(device):
    try:
        result = subprocess.run(
            ["npu-smi", "info", "-t", "proc-mem", "-i", str(device)],
            capture_output=True, text=True, timeout=6, env=dict(os.environ, LC_ALL="C"),
        )
    except (OSError, subprocess.TimeoutExpired):
        return "?"
    if result.returncode != 0:
        return "?"
    if re.search(r"Process id\s*:", result.stdout, re.I):
        return "B"
    if re.search(rf"NPU ID\s*:\s*{device}(?:\s|$)", result.stdout, re.I):
        return "I"
    return "?"


def occupancy():
    with ThreadPoolExecutor(max_workers=16) as pool:
        return "".join(pool.map(probe, range(16)))


def process(host, args):
    return args if host is None else [*SSH, "--", host, shlex.join(args)]


def remote_python(host, *args):
    return process(host, ["/usr/bin/python3", str(Path(__file__).resolve()), *args])


def bounded_output(args):
    try:
        result = subprocess.run(args, check=True, capture_output=True, timeout=12, text=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def write_status(**values):
    message = dict(time=datetime.now(timezone.utc).isoformat(), **values)
    path = BASE / "status.json"
    temp = path.with_suffix(".part")
    text = json.dumps(message)
    temp.write_text(text + "\n", encoding="utf-8")
    temp.replace(path)
    with (BASE / "campaign.jsonl").open("a", encoding="utf-8") as log:
        log.write(text + "\n")


def case_name(case):
    branch, op, bs, variant = case[:4]
    token = f"-max{case[4]}" if len(case) == 5 else ""
    return f"{branch}-{op}-bs{bs}{token}-{variant}"


def package(branch, variant, max_tokens=None):
    key = f"{branch}-one-{variant}" if branch in ("stages", "dispatch") and max_tokens == 1 else f"{branch}-{variant}"
    return BASE / "installs" / key


def case_package(case):
    return package(case[0], case[3], case[4] if len(case) == 5 else None)


def start_rank(rank, directory, case, rendezvous, capture, route):
    branch, op, bs, variant = case[:4]
    max_tokens = case[4] if len(case) == 5 else (4 if branch == "sleep" else 2)
    install = case_package(case)
    runtime = package(branch, variant)
    device = rank % 16
    records = directory / f"rank-{rank:02}"
    records.mkdir()
    env = dict(os.environ, **ENV)
    env.update(
        RANK=str(rank), DEVICE_ID=str(device), TILEXR_COMM_ID=rendezvous,
        TILEXR_RDMA_DEVICE=f"hrn5_{device // 2}", TILEXR_UBX_BS=str(bs),
        TILEXR_UBX_CAPTURE=str(int(variant == "on")),
        TILEXR_UBX_MAX_TOKENS=str(max_tokens),
        TILEXR_UBX_CLOCK_START=str(int(branch != "stages")),
        TILEXR_UBX_ROUTES_FILE=str(route),
        LD_LIBRARY_PATH=os.pathsep.join([str(install / "lib"), str(runtime / "lib"),
                                        env.get("LD_LIBRARY_PATH", "")]),
    )
    if branch == "sleep":
        application = [str(install / "bin/ubx_combine_write"), "combine-only", str(records)]
    else:
        if branch == "stages":
            env["TILEXR_UBX_COMBINE"] = op
        application = [str(install / "bin/ubx_full_intra"),
                       "fp8e4m3" if op == "dispatch" else "fp16", str(records), capture]
    if variant == "on":
        env["TILEXR_UBX_COMBINE_CAPTURE"] = "1"
        env["TILEXR_UBX_CAPTURE_ID"] = capture
        if branch in TAIL_BRANCHES:
            env["TILEXR_UBX_PACKET_CAPTURE"] = "1"
            env["TILEXR_UBX_PACKET_TRACE_RECORDS"] = "4096"
    command = ["msprof", "--application=" + shlex.join(application),
               "--output=" + str(records / "msprof"), "--task-time=on"]
    setup = 'source /usr/local/Ascend/cann-9.2.T101/set_env.sh && exec "$@"'
    with (records / "stdout.log").open("w") as out, (records / "stderr.log").open("w") as err:
        return subprocess.Popen(["/bin/bash", "-c", setup, "ubx", *command],
                                env=env, stdout=out, stderr=err, start_new_session=False)


def rank_records(folder, case, rank):
    branch, op, _, _ = case[:4]
    if branch == "dispatch" or branch in TAIL_BRANCHES:
        candidates = folder.glob("*.summary.json")
    elif branch == "stages":
        candidates = folder.glob(f"combine-{op}-rank-{rank}-round-*.json")
    else:
        candidates = folder.glob(f"combine-rank-{rank}-round-*.json")
    records = {}
    for file in sorted(candidates):
        if "clock" in file.name:
            continue
        item = json.loads(file.read_text(encoding="utf-8"))
        if item.get("correctness") is not True or item.get("native") is not True:
            raise ValueError(f"rank {rank} reported invalid correctness or native status")
        if item["rank"] != rank or item["batch"] != case[2]:
            raise ValueError(f"rank {rank} reported wrong identity")
        if len(case) == 5 and (item.get("max_tokens") != case[4] or
                               item.get("effective_max_tokens") != case[4]):
            raise ValueError(f"rank {rank} has inconsistent configured/effective packet cap")
        if branch in TAIL_BRANCHES:
            expected_capture = case[3] == "on"
            if (item.get("max_tokens") != 2 or item.get("dtype") != "fp8e4m3"
                    or item.get("packet_trace") is not expected_capture
                    or item.get("packet_trace_records") != (4096 if expected_capture else 0)):
                raise ValueError(f"rank {rank} has inconsistent Dispatch trace settings")
        round_id = item["round"]
        if round_id in records or item["warmup"] != (round_id < 5):
            raise ValueError(f"rank {rank} reported bad warmup/round identity")
        records[round_id] = item
    if set(records) != set(range(25)):
        raise ValueError(f"rank {rank} produced {len(records)} of 25 required rounds")
    return records


def run_node(index, capture, rendezvous, case):
    branch, _, bs, variant = case[:4]
    directory = BASE / "runs" / case_name(case)
    directory.mkdir(parents=True, exist_ok=False)
    route = directory / "routes.bin"
    generator = BASE / "trees" / branch / "tools/ep/generate_ubx_routes.py"
    subprocess.run([sys.executable, str(generator), "--ep", "32", "--bs", str(bs),
                    "--experts-per-rank", "12", "--seed", "261011", "--output", str(route)],
                   check=True, stdout=subprocess.DEVNULL, timeout=20)
    start = index * 16
    ranks = range(start, start + 16)
    children = [start_rank(rank, directory, case, rendezvous, capture, route) for rank in ranks]
    deadline = time.monotonic() + 900
    while any(child.poll() is None for child in children):
        if any(child.poll() not in (0, None) for child in children) or time.monotonic() >= deadline:
            raise RuntimeError("rank failure or rank deadline")
        time.sleep(0.5)
    if any(child.returncode != 0 for child in children):
        raise RuntimeError("rank failed")
    rounds = {rank: rank_records(directory / f"rank-{rank:02}", case, rank) for rank in ranks}
    if variant == "on":
        for rank in ranks:
            traces = list((directory / f"rank-{rank:02}").glob("*.records.bin"))
            if len(traces) != 25:
                raise RuntimeError(f"rank {rank} has {len(traces)} of 25 captures")
    node = directory / f"node-{index}.json"
    partial = node.with_suffix(".part")
    partial.write_text(json.dumps(rounds), encoding="utf-8")
    partial.replace(node)


def summarize(case, all_ranks):
    branch, _, _, _ = case[:4]
    if branch in TAIL_BRANCHES:
        branch = "dispatch"
    time_key = {"dispatch": "dispatch_event_us", "stages": "combine_stream_us", "sleep": "combine_us"}[branch]
    byte_key = {"dispatch": "rdma_data_bytes", "stages": "return_bytes", "sleep": "rdma_wire_bytes"}[branch]
    all_samples = [all_ranks[str(rank)][str(round_id)] for rank in range(32) for round_id in range(5, 25)]
    unit = "stream_event_us"
    per_round = []
    for round_id in range(5, 25):
        values = [all_ranks[str(rank)][str(round_id)][time_key] for rank in range(32)]
        per_round.append(max(values))
    order = sorted(range(20), key=per_round.__getitem__)
    stats = lambda xs: {"min": min(xs), "median": median(xs), "max": max(xs)}
    sample = all_samples[0]
    return {
        "case": case_name(case), "unit": unit, "samples": len(all_samples),
        **({"max_tokens": case[4], "effective_max_tokens": case[4]} if len(case) == 5 else {}),
        "backend": sample["backend"], "native": sample["native"],
        "abi": sample.get("kernel_abi", sample.get("abi")),
        "rank_round": stats([row[time_key] for row in all_samples]),
        "round_slowest_rank": stats(per_round), "rdma_bytes": stats([row[byte_key] for row in all_samples]),
        "rdma_GBps": stats([row[byte_key] / row[time_key] / 1000 for row in all_samples if row[time_key] > 0]),
        "typical_rounds": dict(zip(("min", "middle_low", "middle_high", "max"),
                                   (5 + order[i] for i in (0, 9, 10, 19)))),
    }


def finalize_case(peer, case):
    directory = BASE / "runs" / case_name(case)
    all_ranks = json.loads((directory / "node-0.json").read_text(encoding="utf-8"))
    remote = subprocess.run(process(peer, ["cat", str(directory / "node-1.json")]),
                            check=True, capture_output=True, timeout=25, text=True)
    all_ranks.update(json.loads(remote.stdout))
    report = summarize(case, all_ranks)
    if case[0] in TAIL_BRANCHES:
        route = directory / "routes.bin"
        route_sha = hashlib.sha256(route.read_bytes()).hexdigest()
        peer_checksum = subprocess.run(process(peer, ["sha256sum", str(route)]),
                                       check=True, capture_output=True, timeout=25, text=True)
        if peer_checksum.stdout.split()[0] != route_sha:
            raise ValueError("generated route input differs between nodes")
        report["route_sha256"] = route_sha
    temporary = directory / "report.part"
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary.replace(directory / "report.json")


def inspect_case(case, index):
    directory = BASE / "runs" / case
    unit = "tilexr-" + BASE.name + "-" + case
    service = subprocess.run(["systemctl", "show", "-p", "ActiveState", "--value", unit],
                             capture_output=True, timeout=8, text=True)
    return {"exists": directory.is_dir(), "done": (directory / f"node-{index}.json").is_file(),
            "active": service.stdout.strip() not in ("inactive", "failed")}


def archive_case(case, attempt):
    directory = BASE / "runs" / case
    if directory.is_dir():
        archive = BASE / "attempts" / attempt
        archive.mkdir(parents=True, exist_ok=True)
        directory.replace(archive / case)


def recover_case(peer, case):
    name = case_name(case)
    while True:
        statuses = [bounded_output(remote_python(host, "inspect-case", name, str(index)))
                    for index, host in enumerate((None, peer))]
        if all(statuses):
            local, other = map(json.loads, statuses)
            if not local["active"] and not other["active"]:
                break
        write_status(phase="recovering_previous_attempt", case=name)
        time.sleep(5)
    if local["done"] and other["done"]:
        finalize_case(peer, case)
        return True
    if local["exists"] or other["exists"]:
        attempt = "interrupted-" + uuid.uuid4().hex
        for host in (peer, None):
            subprocess.run(remote_python(host, "archive-case", name, attempt),
                           timeout=15, check=True)
    return False


class TransportError(RuntimeError):
    pass


def run_pair(peer, coordinator_ip, case):
    name = case_name(case)
    unit = "tilexr-" + BASE.name + "-" + name
    capture = uuid.uuid4().hex
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((coordinator_ip, 0))
        port = sock.getsockname()[1]
    rendezvous = f"{coordinator_ip}:{port}"
    tasks = [subprocess.Popen(process(host, [
        "systemd-run", "--quiet", "--wait", "--collect", "--property=Type=exec",
        "--property=RuntimeMaxSec=960", "--unit=" + unit,
        "/usr/bin/python3", str(Path(__file__).resolve()), "run-node", str(index), capture,
        rendezvous, *map(str, case)])) for index, host in enumerate((None, peer))]
    deadline = time.monotonic() + 985
    try:
        while True:
            codes = [child.poll() for child in tasks]
            if codes == [0, 0]:
                break
            if case[0] in ABC_BRANCHES and codes[1] == 255:
                raise TransportError(f"per-node services failed: {codes}")
            if any(code not in (0, None) for code in codes) or time.monotonic() >= deadline:
                raise RuntimeError(f"per-node services failed: {codes}")
            time.sleep(1)
        finalize_case(peer, case)
    finally:
        for host, task in zip((None, peer), tasks):
            if task.poll() != 0:
                bounded_output(process(host, ["systemctl", "stop", unit]))


def run_campaign(coordinator, peer):
    (BASE / "runs").mkdir(exist_ok=True)
    completed, failed = 0, 0
    for case in CASES:
        name = case_name(case)
        case_dir = BASE / "runs" / name
        if (case_dir / "report.json").is_file():
            completed += 1
            continue
        if (case_dir / "failure.json").is_file():
            failed += 1
            continue
        if recover_case(peer, case):
            completed += 1
            continue
        retry_marker = BASE / f"{name}.transport-retried"
        while True:
            ready = str(BASE / "ready" / case_package(case).name)
            if case[0] in ABC_BRANCHES:
                ready = str(BASE / "build.exit")
            while True:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    remote = pool.submit(bounded_output, remote_python(peer, "probe", ready))
                    own = occupancy()
                    other = remote.result().split()
                peer_ready, other_status = other if len(other) == 2 else ("0", "?" * 16)
                local_ready = ready_file(Path(ready))
                if local_ready < 0 or peer_ready == "-1":
                    write_status(phase="build_failed", case=name, local_build=local_ready,
                                 peer_build=peer_ready)
                    time.sleep(30)
                    continue
                if local_ready == 1 and peer_ready == "1":
                    phase = "checking_occupancy"
                    if own == other_status == "I" * 16:
                        break
                else:
                    phase = "waiting_for_build"
                write_status(phase=phase, case=name, coordinator=own, peer=other_status)
                time.sleep(5)
            write_status(phase="running", case=name, coordinator=own, peer=other_status)
            try:
                run_pair(peer, coordinator, case)
            except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                if isinstance(exc, TransportError) and not retry_marker.exists():
                    retry_marker.touch()
                    write_status(phase="retrying_transport", case=name)
                    if recover_case(peer, case):
                        completed += 1
                        break
                    continue
                case_dir.mkdir(exist_ok=True)
                failure = {"case": name, "reason": str(exc)}
                (case_dir / "failure.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
                failed += 1
                write_status(phase="case_failed", case=name, completed=completed, failed=failed)
            else:
                completed += 1
                write_status(phase="case_completed", case=name, completed=completed, failed=failed)
            break
    write_status(phase="completed", cases=len(CASES), completed=completed, failed=failed)


def ready_file(path):
    if path.name == "build.exit":
        try:
            return 1 if path.read_text().strip() == "0" else -1
        except FileNotFoundError:
            return 0
    return int(path.is_file())


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "probe":
        print(f"{ready_file(Path(sys.argv[2]))} {occupancy()}")
    elif mode == "inspect-case":
        print(json.dumps(inspect_case(sys.argv[2], int(sys.argv[3]))))
    elif mode == "archive-case":
        archive_case(*sys.argv[2:])
    elif mode == "run-node":
        index, capture, rendezvous, branch, op, bs, variant, *max_tokens = sys.argv[2:]
        case = (branch, op, int(bs), variant, *(int(value) for value in max_tokens))
        run_node(int(index), capture, rendezvous, case)
    elif mode == "campaign":
        try:
            run_campaign(*sys.argv[2:])
        except BaseException as error:
            write_status(phase="failed", reason=str(error))
            raise
