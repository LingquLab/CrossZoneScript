"""Readiness checks and foreground launch of the pinned device verifier."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import traceback

ROOT = Path("/root/ubx-replicate4-e172cd2-bs32-f50")
REVISION = "e172cd211889e6da70e35da315771508a1a92c47"
HARDWARE = ROOT / "source/dispatch/dispatch-release/tests/hardware"
BUILD = ROOT / "dispatch-build"
OUTPUT = ROOT / "full-bs32-f50"
PORT = 43000
NICS = [f"hrn5_{device // 2}" for device in range(16)]
RUNTIME_INSTALL = ROOT / "runtime-install"
RUNTIME_REVISION = "-"
ROUTE_MODE = "replicate4"
ALLOW_ALARM = False


def load_json(path):
    return json.loads(path.read_text())


def occupants():
    pids = set()
    for device in range(16):
        process_text = subprocess.check_output(
            ["npu-smi", "info", "-t", "proc-mem", "-i", str(device)], text=True, timeout=6,
        )
        assert re.search(rf"NPU ID\s*:\s*{device}\b", process_text), f"NPU {device} identity unknown"
        found = {int(pid) for pid in re.findall(r"Process id\s*:\s*(\d+)", process_text, re.I)}
        assert found or "No process in device" in process_text, f"NPU {device} occupancy unknown"
        pids.update(found)
    return pids


def start_time(pid):
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return stat.rsplit(") ", 1)[1].split()[19]


def clear_occupants(pids):
    assert all(pid > 1 and pid not in (os.getpid(), os.getppid()) for pid in pids)
    original = {pid: start_time(pid) for pid in pids}
    report = {}
    for sig, name in ((signal.SIGTERM, "term_sent"), (signal.SIGKILL, "kill_sent")):
        report[name] = 0
        for pid in sorted(occupants() & original.keys()):
            identity = original[pid]
            if identity is None or start_time(pid) != identity:
                continue
            try:
                os.kill(pid, sig)
                report[name] += 1
            except ProcessLookupError:
                pass
        if report[name]:
            time.sleep(4)
    (ROOT / f"{OUTPUT.name}-npu-cleanup.json").write_text(json.dumps(report))
    return report


def preflight(cleanup=False):
    assert not OUTPUT.exists(), "Prior execution directory exists"
    receipt = load_json(ROOT / "build-result.json")
    assert receipt["built"] and receipt["revision"] == REVISION
    for name, digest in receipt["artifacts"].items():
        assert hashlib.sha256((BUILD / name).read_bytes()).hexdigest() == digest, name
    if RUNTIME_REVISION != "-":
        receipt = load_json(RUNTIME_INSTALL.parent / "runtime-result.json")
        assert receipt["revision"] == RUNTIME_REVISION
    library, = (p for p in RUNTIME_INSTALL.glob("lib*/libtile-comm.so") if p.is_file())
    digest = hashlib.sha256(library.read_bytes()).hexdigest()
    assert digest == receipt["runtime_sha256"]
    pids = occupants()
    cleanup_report = {"term_sent": 0, "kill_sent": 0}
    if pids and cleanup:
        cleanup_report = clear_occupants(pids)
        pids = occupants()
    assert not pids, "NPU occupied after preflight"
    alarms = []
    for device in range(16):
        health = subprocess.check_output(
            ["npu-smi", "info", "-t", "health", "-i", str(device)], text=True, timeout=6,
        )
        match = re.search(r"Health Status\s*:\s*(\w+)", health, re.I)
        assert match, f"NPU {device} health unknown"
        status = match.group(1).lower()
        if status == "alarm" and ALLOW_ALARM:
            codes = re.search(r"Error Code\s*:\s*([^\r\n]+)", health, re.I)
            alarms.append(dict(device=device, codes=codes.group(1).split() if codes else []))
        else:
            assert status == "ok", f"NPU {device} health is {status}"
    port_count = 1282 if ROUTE_MODE == "replicate16" else 514
    for port in range(PORT, PORT + port_count):
        with socket.socket() as probe:
            probe.bind(("0.0.0.0", port))
    print(json.dumps({"ready": True, "idle": 16, "healthy": 16-len(alarms), "alarms": alarms, "port": PORT,
                      "runtime_sha256": digest, **cleanup_report}))


def run(index):
    if RUNTIME_REVISION != "-":
        linked = subprocess.check_output(
            ["ldd", str(BUILD / "ubx_dispatch_hardware_ep32")], text=True, timeout=15,
        )
        match = re.search(r"libtile-comm\.so\s*=>\s*(\S+)", linked)
        assert match and RUNTIME_INSTALL.resolve() in Path(match.group(1)).resolve().parents, linked
    clock_results_path = HARDWARE / "clock_results.py"
    sys.modules["clock_results"] = load_clock_results(clock_results_path.read_text())
    sys.path.insert(0, str(HARDWARE))
    import run_dispatch_node

    os.environ.pop("ASCEND_VISIBLE_DEVICES", None)
    os.environ.pop("ASCEND_RT_VISIBLE_DEVICES", None)
    log_path = ROOT / f"{OUTPUT.name}-controller.log"
    with log_path.open("x") as log:
        from contextlib import redirect_stdout, redirect_stderr
        with redirect_stdout(log), redirect_stderr(log):
            sys.argv = [
                "run_dispatch_node.py", "--host-index", str(index), "--local-ranks", "16",
                "--devices", *(str(i) for i in range(16)), "--coordinator", "90.90.93.188",
                "--port", str(PORT), "--build", str(BUILD), "--output", str(OUTPUT), "--bs", "32",
                "--nics", *NICS, "--tick-hz", "1000000000", "--revision", REVISION,
                "--measured", "50", "--no-stats", "--clock-install", str(ROOT / "clock-install"),
                "--profile-mode", "full",
            ]
            try:
                run_dispatch_node.main()
            except Exception:
                traceback.print_exc()
                raise
    receipt = load_json(OUTPUT / "host-result.json")
    assert receipt["passed"] and receipt["clock_aligned"] and receipt["revision"] == REVISION
    assert receipt["warmup"] == 10 and receipt["measured"] == 50
    assert receipt["profile_mode"] == "full" and receipt["stage_details_enabled"]
    assert not receipt["statistics_enabled"] and receipt.get("route_distribution", "same_lane") == ROUTE_MODE
    assert len(receipt["ranks"]) == 16 and len(load_json(OUTPUT / "archive-members.json")) == 49
    route_qps = {"same_lane": 1, "replicate4": 4, "replicate16": 16}[ROUTE_MODE]
    for rank in receipt["ranks"]:
        node = rank["rank"]
        assert rank["verified"] and rank["verified_rounds"] == 60
        directory = OUTPUT / f"rank{node}"
        trace = directory / "dispatch.last.trace.json"
        assert hashlib.sha256(trace.read_bytes()).hexdigest() == rank["trace_sha256"]
        queues = load_json(directory / "route-rdma-queue.json")
        if ROUTE_MODE == "same_lane":
            assert queues["relay_core"] == 0 and queues["cq_consumer"] == 60
            assert queues["error"] == 0 and queues["producer"] == queues["completed"] > 0
            continue
        assert len(queues) == route_qps
        for group, queue in enumerate(queues):
            expected = dict(relay_core=group, target_lane=group, producer=120,
                            completed=120, cq_consumer=60, error=0)
            if ROUTE_MODE == "replicate4":
                expected.update(target_group=group, target_lane=4 * group + node % 4)
            assert queue == expected
    print(json.dumps({"passed": True, "index": index, "ranks": 16, "route_qps_verified": 16 * route_qps}))


if __name__ == "__main__":
    action = sys.argv[1]
    PORT = int(sys.argv[3])
    run_name = sys.argv[4]
    assert re.fullmatch(r"full-bs32-f50(?:-[a-z0-9-]+)?", run_name)
    if len(sys.argv) > 5:
        RUNTIME_INSTALL = Path(sys.argv[5])
        RUNTIME_REVISION = sys.argv[6]
    if len(sys.argv) > 7:
        ROOT = Path(sys.argv[7])
        REVISION = sys.argv[8]
        HARDWARE = ROOT / "source/dispatch/dispatch-release/tests/hardware"
        BUILD = ROOT / "dispatch-build"
    if len(sys.argv) > 9:
        ROUTE_MODE = sys.argv[9]
        assert ROUTE_MODE in ("same_lane", "replicate4", "replicate16")
    ALLOW_ALARM = len(sys.argv) > 10 and sys.argv[10] == "allow-alarm"
    OUTPUT = ROOT / run_name
    if action in ("preflight", "preflight-cleanup"):
        preflight(cleanup=action == "preflight-cleanup")
    else:
        assert action == "run"
        run(int(sys.argv[2]))
