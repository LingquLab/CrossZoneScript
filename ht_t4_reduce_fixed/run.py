"""Run the two prebuilt T4/H16 objects in the same CPU SST setup."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
runner = (root / "remote.py").read_text()
ssh = [
    "ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
    "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-",
]
results = {}
for side in ("opt",):
    identity = json.loads((root / f"{side}.json").read_text())
    archive = (root / f"{side}.tar.gz").read_bytes()
    assert hashlib.sha256(archive).hexdigest() == identity["archive_sha256"]
    source = f"IDENTITY = {identity!r}\nPACKAGE = {base64.b64encode(archive).decode()!r}\n" + runner
    compile(source, "<sst_t4_reduce>", "exec")
    try:
        proc = subprocess.run(ssh, input=source.encode(), capture_output=True, timeout=320)
    except subprocess.TimeoutExpired:
        results[side] = {"transport_timeout": True}
        continue
    if proc.returncode:
        results[side] = {"ssh_exit": proc.returncode, "stderr_bytes": len(proc.stderr)}
        continue
    results[side] = json.loads(proc.stdout)
print(json.dumps(results, separators=(",", ":")))
