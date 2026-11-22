"""Compare each HT scheduling parameter with the same blue-built T96 inputs."""

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
assert sys.argv[1:2] == ["--args-file"] and len(sys.argv) == 3
args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
mode = args["mode"]
assert mode in {"run", "status"}
forward_only = args.get("forward_only", False)
assert isinstance(forward_only, bool)
resource = root / "resources"
inputs = resource / "dense_schedule"
identity = json.loads((inputs / "fixture-identity.json").read_text())
assert identity["tokens"] == 96 and identity["hidden"] == 256
request = {"mode": mode, "identity": identity, "variants": {}, "forward_only": forward_only}
if mode == "run":
    request["metadata_variants"] = [
        json.loads((resource / variant / "build-record.json").read_text())
        for variant in ("forward_page", "range_cache")
    ]
    archive = (inputs / "fixture-images.tar.gz").read_bytes()
    assert hashlib.sha256(archive).hexdigest() == identity["input_archive_sha256"]
    request["archive"] = base64.b64encode(archive).decode("ascii")
variants = ("forward_page",) if forward_only else ("drain_count_cache", "forward_page", "batch16", "visit4", "lane_balanced")
for tag in variants:
    folder = resource / tag
    entry = {"build": json.loads((folder / "build-record.json").read_text())}
    if mode == "run":
        entry["object"] = base64.b64encode((folder / "inter_server_send.o.gz").read_bytes()).decode("ascii")
    request["variants"][tag] = entry
runner = (root / "run_mte_t4_remote.py").read_text(encoding="utf-8").rsplit("if __name__ == ", 1)[0]
runner = runner.replace("assert tokens == 4", "assert tokens == 96")
assert 'assert tokens == 96' in runner
status = (root / "run_queue_batch_remote.py").read_text(encoding="utf-8")
prepare = (root / "run_queue_batch_multi_remote.py").read_text(encoding="utf-8").split("def run_multichunk(", 1)[0]
prepare = prepare.replace("== 257", "== 96").replace("== 5", "== 4")
prepare = prepare.replace("t257-queue-batch-data", "t96-ht-schedule-data")
logic = (root / "run_schedule_ab_remote.py").read_text(encoding="utf-8")
source = runner + "\n" + status + "\n" + prepare + "\n" + logic
source += "\nprint(json.dumps(run_schedule(" + repr(request) + "), separators=(',', ':')))\n"
compile(source, "<schedule_runner>", "exec")

command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode("utf-8"), capture_output=True,
                      timeout=14500 if mode == "run" else 45)
if proc.returncode:
    print(json.dumps({"remote_exit_code": proc.returncode, "stderr_bytes": len(proc.stderr)}))
    raise SystemExit(1)
result = json.loads(proc.stdout)
print(json.dumps(result, separators=(",", ":")))
if mode == "run" and any(row.get("run", {}).get("status") != "sst_and_checker_passed"
                         for row in result.values()):
    raise SystemExit(1)
