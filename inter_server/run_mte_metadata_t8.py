"""Run the pinned T8 fixture in CPU SST after qualifying the same object on T4."""

import hashlib
import json
from pathlib import Path
import subprocess

HOST = "root@141.61.94.58"
MOUNT = "/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native"
root = Path(__file__).resolve().parent
resources = root / "resources"
input_package = resources / "mte_metadata_t8"
identity = json.loads((input_package / "identity.json").read_text())
archive = input_package / "inputs.tar.gz"
assert hashlib.sha256(archive.read_bytes()).hexdigest() == identity["archive_sha256"]
is_scalar = "scalar" in Path(__file__).stem
product = "mte_scalar_t4" if is_scalar else "mte_metadata_t4"
case_name = "t4-sst-mte-scalar-forward-5a6c7c8c" if is_scalar else "t4-sst-mte-split-forward-0d3c8694"
build = json.loads((resources / product / "build-record.json").read_text())

options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10"]
wait_for_t4 = r"""
import json
from pathlib import Path
import time

case = Path(MOUNT) / CASE_NAME
deadline = time.monotonic() + 1100
while time.monotonic() < deadline:
    try:
        receipt = json.loads((case / "task-result.json").read_text())
    except (FileNotFoundError, ValueError):
        time.sleep(10)
        continue
    run = receipt.get("run", {})
    print(json.dumps({"status": run.get("status", "t4_run_status_missing"),
                      "returncode": run.get("returncode"),
                      "guard_count": run.get("guards", {}).get("count"),
                      "kernel_sha256": receipt.get("kernel_sha256"),
                      "container_stopped": receipt.get("container_stopped")}))
    break
else:
    print(json.dumps({"status": "t4_terminal_receipt_pending"}))
"""
source = f"MOUNT = {MOUNT!r}\nCASE_NAME = {case_name!r}\n" + wait_for_t4
probe = subprocess.run(["ssh", "-T", *options, HOST, "python3", "-"],
                       input=source.encode("ascii"), capture_output=True, timeout=1130, check=True)
t4 = json.loads(probe.stdout)
if (t4["status"] != "sst_and_checker_passed"
        or t4.get("kernel_sha256") != build["kernel_sha256"] or not t4.get("container_stopped")):
    print(json.dumps({"t8_started": False, "t4": t4}, ensure_ascii=True))
    raise SystemExit(0)

remote_archive = MOUNT + "/t8-inputs-" + identity["archive_sha256"][:12] + ".tar.gz"
subprocess.run(["scp", "-q", *options, str(archive), HOST + ":" + remote_archive],
               capture_output=True, timeout=180, check=True)

runner = (root / "run_mte_t4_remote.py").read_text().rsplit("if __name__ == ", 1)[0]
stage = (root / "prepare_t8_remote.py").read_text()
request = {"tokens": 8, "build": build, "identity": identity}
source = runner + "\n" + stage + "\nmain(" + repr(request) + ")\n"
compile(source, "<t8_runner>", "exec")
proc = subprocess.run(["ssh", "-T", *options, HOST, "python3", "-"],
                      input=source.encode("utf-8"), capture_output=True, timeout=2090)
proc.check_returncode()
result = json.loads(proc.stdout)
result["t4"] = t4
print(json.dumps(result, ensure_ascii=True))
if result.get("run", {}).get("status") != "sst_and_checker_passed":
    raise SystemExit(1)
