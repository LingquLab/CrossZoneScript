"""Run the blue-built single-transfer forward layout in CPU SST."""

import base64
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
resources = root / "resources" / "bulk_forward"
build = json.loads((resources / "build-record.json").read_text(encoding="utf-8"))
layout = json.loads((resources / "layout.json").read_text(encoding="utf-8"))
obj = (resources / "inter_server_send.o.gz").read_bytes()
images = (resources / "forward-images.tar.gz").read_bytes()
assert hashlib.sha256(gzip.decompress(obj)).hexdigest() == build["kernel_sha256"]
assert hashlib.sha256(images).hexdigest() == layout["archive_sha256"]

request = dict(build=build, layout=layout, object=base64.b64encode(obj).decode("ascii"),
               forward_images=base64.b64encode(images).decode("ascii"))
runner = (root / "run_mte_t4_remote.py").read_text(encoding="utf-8").rsplit("if __name__ == ", 1)[0]
stage = (root / "run_bulk_forward_remote.py").read_text(encoding="utf-8")
source = runner + "\n" + stage + "\nprint(json.dumps(run_bulk(" + repr(request) + "), ensure_ascii=True))\n"
compile(source, "<bulk_forward_runner>", "exec")
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode("utf-8"), capture_output=True, timeout=4050)
proc.check_returncode()
result = json.loads(proc.stdout)
print(json.dumps(result, ensure_ascii=True))
if not all(result.get(case, {}).get("run", {}).get("status") == "sst_and_checker_passed"
           for case in ("t4", "t8")):
    raise SystemExit(1)
