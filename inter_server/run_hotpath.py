"""Run or inspect the EP64 comparison using the retained SST input images."""

import base64
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys


root = Path(__file__).resolve().parent
resources = root / "resources" / "hotpath"
build = json.loads((resources / "build-record.json").read_text(encoding="utf-8"))
assert sys.argv[1:2] == ["--args-file"] and len(sys.argv) == 3
mode = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")).get("mode", "run")
assert mode in ("run", "status")
request = {"build": build, "mode": mode}
if mode == "run":
    compressed = (resources / "inter_server_send.o.gz").read_bytes()
    assert hashlib.sha256(gzip.decompress(compressed)).hexdigest() == build["kernel_sha256"]
    request["object"] = base64.b64encode(compressed).decode("ascii")

runner = (root / "run_mte_t4_remote.py").read_text(encoding="utf-8").rsplit("if __name__ == ", 1)[0]
logic = (root / "run_hotpath_remote.py").read_text(encoding="utf-8")
source = runner + "\n" + logic + "\nprint(json.dumps(run_hotpath(" + repr(request) + "), ensure_ascii=True))\n"
compile(source, "<hotpath_runner>", "exec")
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode("utf-8"), capture_output=True,
                      timeout=4050 if mode == "run" else 35)
if proc.returncode:
    print(json.dumps({"remote_exit_code": proc.returncode, "stderr_bytes": len(proc.stderr)}))
    raise SystemExit(1)
result = json.loads(proc.stdout)
print(json.dumps(result, ensure_ascii=True))
if mode == "run" and not all(
    result.get(case, {}).get("run", {}).get("status") == "sst_and_checker_passed"
    for case in ("t4", "t8")
):
    raise SystemExit(1)
