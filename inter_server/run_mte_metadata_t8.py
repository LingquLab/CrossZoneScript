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
build = json.loads((resources / "mte_metadata_t4" / "build-record.json").read_text())
assert identity["kernel_sha256"] == build["kernel_sha256"]

options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10"]
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
print(json.dumps(result, ensure_ascii=True))
if result.get("run", {}).get("status") != "sst_and_checker_passed":
    raise SystemExit(1)
