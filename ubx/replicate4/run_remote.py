"""Readiness checks and foreground launch of the pinned device verifier."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

ROOT = Path("/root/ubx-replicate4-e172cd2-bs32-f50")
REVISION = "e172cd211889e6da70e35da315771508a1a92c47"
HARDWARE = ROOT / "source/dispatch/dispatch-release/tests/hardware"
BUILD = ROOT / "dispatch-build"
OUTPUT = ROOT / "full-bs32-f50"
PORT = 43000
NICS = [f"hrn5_{device // 2}" for device in range(16)]


def load_json(path):
    return json.loads(path.read_text())


def preflight():
    assert not OUTPUT.exists(), "Prior execution directory exists"
    receipt = load_json(ROOT / "build-result.json")
    assert receipt["built"] and receipt["revision"] == REVISION
    for name, digest in receipt["artifacts"].items():
        assert hashlib.sha256((BUILD / name).read_bytes()).hexdigest() == digest, name
    library, = (p for p in (ROOT / "runtime-install").glob("lib*/libtile-comm.so") if p.is_file())
    assert hashlib.sha256(library.read_bytes()).hexdigest() == receipt["runtime_sha256"]
    for device in range(16):
        process_text = subprocess.check_output(
            ["npu-smi", "info", "-t", "proc-mem", "-i", str(device)], text=True, timeout=6,
        )
        assert re.search(rf"NPU ID\s*:\s*{device}\b", process_text), f"NPU {device} identity unknown"
        assert "No process in device" in process_text and not re.search(
            r"Process id\s*:", process_text, re.I
        ), f"NPU {device} occupied or unrecognized"
        health = subprocess.check_output(
            ["npu-smi", "info", "-t", "health", "-i", str(device)], text=True, timeout=6,
        )
        # proc-mem has the device ID; some health versions only print health fields.
        assert re.search(r"Health Status\s*:\s*OK\b", health), f"NPU {device} not healthy"
    for port in range(PORT, PORT + 514):
        with socket.socket() as probe:
            probe.bind(("0.0.0.0", port))
    print(json.dumps({"ready": True, "idle": 16, "healthy": 16, "port": PORT}))


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
            run_dispatch_node.main()
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
    if action == "preflight":
        preflight()
    else:
        assert action == "run"
        run(int(sys.argv[2]))
