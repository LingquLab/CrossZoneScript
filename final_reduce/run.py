"""Transfer the verified blue archive and run the CPU-only SST cases."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

folder = Path(__file__).resolve().parent
identity = json.loads((folder / "identity.json").read_text())
archive = (folder / "cases.tar.gz").read_bytes()
assert hashlib.sha256(archive).hexdigest() == identity["archive_sha256"]
remote = (folder / "remote.py").read_text()
source = f"IDENTITY = {identity!r}\nPACKAGE = {base64.b64encode(archive).decode()!r}\n" + remote
compile(source, "<final_reduce_sst>", "exec")
ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
       "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
p = subprocess.run(ssh, input=source.encode(), capture_output=True, timeout=540)
if p.returncode:
    err = p.stderr.decode(errors="replace").lower()
    print(json.dumps(dict(ssh_exit=p.returncode, timed_out="timed out" in err, bytes=len(p.stderr))))
else:
    print(json.dumps(json.loads(p.stdout), separators=(",", ":")))
