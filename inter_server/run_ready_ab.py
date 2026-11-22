"""Compare cached reducer epochs with preset and staggered ready flags."""
import base64
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
assert sys.argv[1:2] == ["--args-file"] and len(sys.argv) == 3
args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
mode = args["mode"]
assert mode in {"run", "status"}
request = {"mode": mode, "variants": {}}
for variant in ("ready_watermark", "ready_stagger_base", "ready_stagger_cache"):
    resources = root / "resources" / variant
    candidate = {"build": json.loads((resources / "build-record.json").read_text())}
    if mode == "run":
        candidate["object"] = base64.b64encode((resources / "inter_server_send.o.gz").read_bytes()).decode("ascii")
    request["variants"][variant] = candidate
runner = (root / "run_mte_t4_remote.py").read_text().rsplit("if __name__ == ", 1)[0]
status = (root / "run_queue_batch_remote.py").read_text()
logic = (root / "run_ready_ab_remote.py").read_text()
source = runner + "\n" + status + "\n" + logic
source += "\nprint(json.dumps(run_ready(" + repr(request) + "), separators=(',', ':')))\n"
compile(source, "<ready_runner>", "exec")
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode("utf-8"), capture_output=True,
                      timeout=8150 if mode == "run" else 45)
if proc.returncode:
    print(json.dumps({"remote_exit_code": proc.returncode, "stderr_bytes": len(proc.stderr)}))
    raise SystemExit(1)
result = json.loads(proc.stdout)
print(json.dumps(result, separators=(",", ":")))
if mode == "run" and any(row.get("run", {}).get("status") != "sst_and_checker_passed"
                         for row in result.values()):
    raise SystemExit(1)
