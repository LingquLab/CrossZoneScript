"""Recover a Perfetto trace from the retained CPU SST dumps."""
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
runner = (root / "run_mte_t4_remote.py").read_text(encoding="utf-8").rsplit("if __name__ == ", 1)[0]
logic = (root / "recover_ht_profile_remote.py").read_text(encoding="utf-8")
source = runner + "\n" + logic + "\nprint(json.dumps(recover(), separators=(',', ':')))\n"
compile(source, "<profile_recovery>", "exec")
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode("utf-8"), capture_output=True, timeout=180)
if proc.returncode:
    print(json.dumps({"remote_exit_code": proc.returncode, "stderr_bytes": len(proc.stderr)}))
    raise SystemExit(1)
print(json.dumps(json.loads(proc.stdout), separators=(",", ":")))
