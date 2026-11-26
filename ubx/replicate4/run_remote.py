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


def clear_occupants(pids):
    assert all(pid > 1 and pid not in (os.getpid(), os.getppid()) for pid in pids)
    descriptors = {}
    terminated, killed = [], []
    try:
        for pid in sorted(pids):
            try:
                descriptors[pid] = os.pidfd_open(pid)
            except ProcessLookupError:
                pass
        for sig, confirmed in ((signal.SIGTERM, terminated), (signal.SIGKILL, killed)):
            current = occupants()
            for pid in sorted(current & descriptors.keys()):
                try:
                    signal.pidfd_send_signal(descriptors[pid], sig)
                    confirmed.append(pid)
                except ProcessLookupError:
                    pass
            if confirmed:
                time.sleep(4)
    finally:
        for descriptor in descriptors.values():
            os.close(descriptor)
    report = {"term_sent": len(terminated), "kill_sent": len(killed)}
    (ROOT / "full-bs32-f50-npu-cleanup.json").write_text(json.dumps(report))
    return report


def preflight(cleanup=False):
    assert not OUTPUT.exists(), "Prior execution directory exists"
    receipt = load_json(ROOT / "build-result.json")
    assert receipt["built"] and receipt["revision"] == REVISION
    for name, digest in receipt["artifacts"].items():
        assert hashlib.sha256((BUILD / name).read_bytes()).hexdigest() == digest, name
    library, = (p for p in (ROOT / "runtime-install").glob("lib*/libtile-comm.so") if p.is_file())
    assert hashlib.sha256(library.read_bytes()).hexdigest() == receipt["runtime_sha256"]
    pids = occupants()
    cleanup_report = {"term_sent": 0, "kill_sent": 0}
    if pids and cleanup:
        cleanup_report = clear_occupants(pids)
        pids = occupants()
    assert not pids, "NPU occupied after preflight"
    for device in range(16):
        health = subprocess.check_output(
            ["npu-smi", "info", "-t", "health", "-i", str(device)], text=True, timeout=6,
        )
        # proc-mem has the device ID; some health versions only print health fields.
        assert re.search(r"Health Status\s*:\s*OK\b", health), f"NPU {device} not healthy"
    for port in range(PORT, PORT + 514):
        with socket.socket() as probe:
            probe.bind(("0.0.0.0", port))
    print(json.dumps({"ready": True, "idle": 16, "healthy": 16, "port": PORT, **cleanup_report}))


def run(index):
    clock_results_path = HARDWARE / "clock_results.py"
    sys.modules["clock_results"] = load_clock_results(clock_results_path.read_text())
    sys.path.insert(0, str(HARDWARE))
    import run_dispatch_node

    os.environ.pop("ASCEND_VISIBLE_DEVICES", None)
    os.environ.pop("ASCEND_RT_VISIBLE_DEVICES", None)
    log_path = ROOT / "full-bs32-f50-controller.log"
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
    assert not receipt["statistics_enabled"] and receipt["route_distribution"] == "replicate4"
    assert len(receipt["ranks"]) == 16 and len(load_json(OUTPUT / "archive-members.json")) == 49
    for rank in receipt["ranks"]:
        node = rank["rank"]
        assert rank["verified"] and rank["verified_rounds"] == 60
        directory = OUTPUT / f"rank{node}"
        trace = directory / "dispatch.last.trace.json"
        assert hashlib.sha256(trace.read_bytes()).hexdigest() == rank["trace_sha256"]
        queues = load_json(directory / "route-rdma-queue.json")
        assert len(queues) == 4
        for group, queue in enumerate(queues):
            assert queue == dict(
                relay_core=group, target_group=group, target_lane=4 * group + node % 4,
                producer=120, completed=120, cq_consumer=60, error=0,
            )
    print(json.dumps({"passed": True, "index": index, "ranks": 16, "route_qps_verified": 64}))


if __name__ == "__main__":
    action = sys.argv[1]
    PORT = int(sys.argv[3])
    if action in ("preflight", "preflight-cleanup"):
        preflight(cleanup=action == "preflight-cleanup")
    else:
        assert action == "run"
        run(int(sys.argv[2]))
