"""Transfer the blue-built Memory Combine fixture and run it in CPU SST."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
package = (root / "case.tar.gz").read_bytes()
record = json.loads((root / "identity.json").read_text())
assert hashlib.sha256(package).hexdigest() == record["archive_sha256"]
remote = (root / "remote.py").read_text()
source = "PACKAGE = " + repr(base64.b64encode(package).decode("ascii")) + "\nIDENTITY = " + repr(record) + "\n" + remote
compile(source, "<combine_memory_cast>", "exec")
proc = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                       "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"],
                      input=source.encode("utf-8"), capture_output=True, timeout=1050)
if proc.returncode:
    print(json.dumps({"remote_exit_code": proc.returncode, "stderr_bytes": len(proc.stderr)}))
    raise SystemExit(1)
print(json.dumps(json.loads(proc.stdout), separators=(",", ":")))
