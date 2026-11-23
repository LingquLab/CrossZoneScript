"""Transfer verified blue-compiled objects and run only the T4 sender workload."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
request = {}
for name in ("base", "opt"):
    build = json.loads((root / name / "build-record.json").read_text())
    obj = (root / name / "inter_server_send.o.gz").read_bytes()
    assert hashlib.sha256(gzip.decompress(obj)).hexdigest() == build["kernel_sha256"]
    request[name] = {"build": build, "object": base64.b64encode(obj).decode("ascii")}
support = root.parent / "inter_server"
source = (support / "run_mte_t4_remote.py").read_text().rsplit("if __name__ == ", 1)[0]
source += (support / "run_queue_batch_remote.py").read_text() + "\n"
source += (root / "remote.py").read_text() + "\n"
source += f"print(json.dumps(run_send({request!r}), separators=(',', ':')))\n"
compile(source, "<ht_t4_send_ab>", "exec")
ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
       "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
try:
    proc = subprocess.run(ssh, input=source.encode(), capture_output=True, timeout=4100)
except subprocess.TimeoutExpired:
    print(json.dumps({"transport_timeout": True}))
else:
    if proc.returncode:
        print(json.dumps({"ssh_exit": proc.returncode, "stderr_bytes": len(proc.stderr)}))
    else:
        print(json.dumps(json.loads(proc.stdout), separators=(",", ":")))
