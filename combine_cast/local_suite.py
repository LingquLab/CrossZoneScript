"""Run one-chip cases through the same installed V310 self-test mode."""
import base64
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
cases = root / "local_suite"
remote = (root / "remote.py").read_text()
ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
       "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]


def execute(manifest):
    record = json.loads(manifest.read_text())
    package = manifest.with_suffix(".tar.gz").read_bytes()
    assert hashlib.sha256(package).hexdigest() == record["archive_sha256"]
    source = "PACKAGE = " + repr(base64.b64encode(package).decode("ascii")) + "\nIDENTITY = " + repr(record) + "\n" + remote
    compile(source, "<combine_memory_cast>", "exec")
    try:
        proc = subprocess.run(ssh, input=source.encode(), capture_output=True, timeout=240)
    except subprocess.TimeoutExpired:
        return {"transport": "timed_out"}
    if proc.returncode:
        return {"transport_exit": proc.returncode, "stderr_bytes": len(proc.stderr)}
    row = json.loads(proc.stdout)
    return {k: row[k] for k in ("status", "sst_exit", "simulated_time", "dump_files", "checker_exit",
                               "cases", "container_removed", "error_type", "log_categories") if k in row}


manifests = sorted(cases.glob("*.json"))
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = dict(zip((m.stem for m in manifests), pool.map(execute, manifests), strict=True))
print(json.dumps(results, separators=(",", ":")))
