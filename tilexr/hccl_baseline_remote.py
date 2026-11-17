"""Qualify official CCU communication once using only the project-local topology."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def device_state(device):
    result = subprocess.run(["npu-smi", "info", "-t", "proc-mem", "-i", str(device)],
                            capture_output=True, text=True, timeout=10)
    state = "indeterminate"
    if result.returncode == 0:
        if re.search(r"Process id\s*:", result.stdout):
            state = "busy"
        elif re.search(r"NPU ID\s*:\s*" + str(device) + r"(?:\s|$)", result.stdout):
            state = "idle"
    return {"device": device, "state": state}


def run(args, record):
    require(args["host"] == "141.61.73.107" and args["devices"] == [2, 3], "invalid_scope")
    rootinfo = Path(args["rootinfo"]).resolve(strict=True)
    require(rootinfo.is_relative_to(Path("/home/c00996840/ai/TileXR-deploy/config")), "nonproject_rootinfo")
    require(digest(rootinfo) == args["rootinfo_sha256"], "rootinfo_changed")
    topology = Path(json.loads(rootinfo.read_text())["topo_file_path"]).resolve(strict=True)
    require(topology.parent == rootinfo.parent and digest(topology) == args["topology_sha256"], "topology_changed")
    # A private bind replaces only this process tree's view, never the system file.
    require(Path("/etc/hccl_rootinfo.json").is_file(), "private_bind_target_missing")
    root = Path("/home/c00996840/ai/TileXR-test") / args["run_id"]
    root.mkdir(exist_ok=False)
    record.update(work_directory=str(root), rootinfo=str(rootinfo), topology=str(topology),
                  rootinfo_sha256=args["rootinfo_sha256"], topology_sha256=args["topology_sha256"],
                  accelerator="ccu_ms", devices=[2, 3], baseline_attempt_count=1)
    identity = {}
    for name, path in [
        ("hccl_test", "/usr/local/Ascend/cann-9.1.0/tools/hccl_test/bin/all_reduce_test"),
        ("hccl_v2", "/usr/local/Ascend/cann-9.1.0/aarch64-linux/lib64/libhccl_v2.so"),
        ("mpi", "/usr/local/mpi/mpich/lib/libmpi.so.12"),
    ]:
        identity[name] = {"path": str(Path(path).resolve()), "sha256": digest(Path(path))}
    record["build_identity"] = identity
    for name, argv in [
        ("cann-version", ["cat", "/usr/local/Ascend/cann-9.1.0/aarch64-linux/ascend_toolkit_install.info"]),
        ("driver-version", ["cat", "/usr/local/Ascend/driver/version.info"]),
        ("npu-2-board", ["npu-smi", "info", "-t", "board", "-i", "2"]),
        ("npu-3-board", ["npu-smi", "info", "-t", "board", "-i", "3"]),
    ]:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        (root / (name + ".log")).write_text(result.stdout + result.stderr)
    record["pre_devices"] = [device_state(device) for device in [2, 3]]
    require(all(item["state"] == "idle" for item in record["pre_devices"]), "selected_devices_not_idle")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("HCCL_", "TILEXR_", "MOONEP_"))
                   and key not in ["ASCEND_VISIBLE_DEVICES", "ASCEND_RT_VISIBLE_DEVICES", "RANK_ID", "RANK_SIZE", "DEVICE_ID"]}
    environment.update({
        "LD_LIBRARY_PATH": "/usr/local/mpi/mpich/lib:" + environment.get("LD_LIBRARY_PATH", ""),
        "PATH": "/usr/local/mpi/mpich/bin:" + environment.get("PATH", ""),
        "HCCL_TEST_USE_DEVS": "2,3", "HCCL_CONNECT_TIMEOUT": "60", "HCCL_EXEC_TIMEOUT": "60",
        "ASCEND_PROCESS_LOG_PATH": str(root / "plog"), "ASCEND_GLOBAL_LOG_LEVEL": "3",
    })
    namespace_script = root / "private-rootinfo.sh"
    namespace_script.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\n'
        'mount --bind "$1" /etc/hccl_rootinfo.json\n'
        'mount -o remount,bind,ro /etc/hccl_rootinfo.json\n'
        'test "$(stat -Lc %d:%i "$1")" = "$(stat -Lc %d:%i /etc/hccl_rootinfo.json)"\n'
        'printf "project_rootinfo_private_bind_verified=1\\n"\n'
        'shift\nexec "$@"\n')
    mpi = "/usr/local/mpi/mpich/bin/mpiexec.hydra"
    binary = "/usr/local/Ascend/cann-9.1.0/tools/hccl_test/bin/all_reduce_test"
    command = ["unshare", "--mount", "--propagation", "private", "bash", str(namespace_script), str(rootinfo),
               mpi, "-launcher", "fork", "-hosts", "localhost", "-l", "-n", "2", binary,
               "-a", "ccu_ms", "-p", "2", "-b", "128", "-e", "512", "-i", "128",
               "-n", "1", "-w", "0", "-d", "fp32", "-o", "sum", "-c", "2"]
    (root / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    started = time.monotonic()
    with (root / "baseline.log").open("wb") as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                   env=environment, cwd=str(root), start_new_session=True)
        try:
            status = process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            status = 124
    text = (root / "baseline.log").read_text(errors="replace")
    lines = text.splitlines()
    record.update(exit_code=status, elapsed_seconds=round(time.monotonic() - started, 3),
                  private_rootinfo_bind_verified="project_rootinfo_private_bind_verified=1" in text,
                  correctness_lines=[line[:500] for line in lines if re.search(r'\bsuccess\s*$', line)][:16],
                  error_lines=[line[:500] for line in lines if re.search(r'error|failed|fault|invalid|denied', line, re.I)][:12],
                  rank_device_lines=[line[:350] for line in lines
                                     if re.search(r'local.?rank|device.?id|rank.?id|accelerator', line, re.I)][:16])
    record["npu_workload_run"] = record["private_rootinfo_bind_verified"] and bool(re.search(r'^\[[01]\]', text, re.M))
    record["post_devices"] = [device_state(device) for device in [2, 3]]
    for _ in range(4):
        if all(item["state"] == "idle" for item in record["post_devices"]):
            break
        time.sleep(2)
        record["post_devices"] = [device_state(device) for device in [2, 3]]
    require(record["private_rootinfo_bind_verified"], "project_rootinfo_namespace_setup_failed")
    require(status == 0, "official_hccl_ccu_baseline_failed")
    require(len(record["correctness_lines"]) >= 4 and not record["error_lines"], "hccl_correctness_not_proven")
    require(all(item["state"] == "idle" for item in record["post_devices"]), "post_baseline_devices_not_idle")
    record["status"] = "PASS"


if __name__ == "__main__":
    settings = json.loads(base64.urlsafe_b64decode(sys.argv[1]).decode())
    summary = {"status": "FAIL", "npu_workload_run": False}
    try:
        run(settings, summary)
    except Exception as error:
        summary.update(error_type=type(error).__name__, reason=str(error)[:160])
    if "work_directory" in summary:
        (Path(summary["work_directory"]) / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("CZ_HCCL_BASELINE " + json.dumps(summary))
    sys.exit(0 if summary["status"] == "PASS" else 1)
