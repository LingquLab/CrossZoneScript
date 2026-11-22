"""Compare T96 expected WQE slots with retained SQ and CQ trace records."""
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
trace = (root / "owner_trace_remote.py").read_text().rsplit("if __name__ == ", 1)[0]
logic = (root / "diagnose_t96_sq_remote.py").read_text()
source = trace + "\n" + logic + "\nprint(json.dumps(diagnose_t96(), separators=(',', ':')))\n"
compile(source, "<t96_sq_evidence>", "exec")
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode("ascii"), capture_output=True, timeout=210)
if proc.returncode:
    print(json.dumps({"remote_exit_code": proc.returncode, "stderr_bytes": len(proc.stderr)}))
    raise SystemExit(1)
print(json.dumps(json.loads(proc.stdout), separators=(",", ":")))
