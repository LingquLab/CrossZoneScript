"""Transfer the pinned T32 fused FP32 workload when the green platform recovers."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

root = Path(__file__).resolve().parent
identity = json.loads((root / "fixture-identity.json").read_text())
fixture = (root / "fixture-images.tar.gz").read_bytes()
object_package = (root / "inter_server_send.o.gz").read_bytes()
assert hashlib.sha256(fixture).hexdigest() == identity["input_archive_sha256"]
assert hashlib.sha256(gzip.decompress(object_package)).hexdigest() == identity["kernel_sha256"]
entry = identity["entry"]
assert entry == "tilexr_ep_ht_combine_sst_fp32"
build = dict(entry=entry, variant="sst-mte-fused-fp32", candidate_commit=identity["commit"],
             kernel_sha256=identity["kernel_sha256"], compiled_in="blue")
request = dict(build=build, identity=identity, archive=base64.b64encode(fixture).decode(),
               object=base64.b64encode(object_package).decode(), checker=(root/"check.py").read_text(),
               exports=(root/"exports.py").read_text())
runner = (root.parent/"inter_server/run_mte_t4_remote.py").read_text().rsplit("if __name__ == ", 1)[0]
assert runner.count("assert tokens == 4") == 1
runner = runner.replace("assert tokens == 4", "assert tokens == 32")
source = "\n".join((runner, (root/"prepare.py").read_text(), (root/"remote.py").read_text(),
                    f"print(json.dumps(run_e2e({request!r}), separators=(',', ':')))" ))
compile(source, "<t32_fused_remote>", "exec")
ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
       "-o", "ConnectTimeout=8", "root@141.61.94.58", "python3", "-"]

deadline = time.monotonic() + 540
failures = []
while True:
    try:
        reply = subprocess.run(ssh, input=source.encode(), capture_output=True, timeout=2200)
    except subprocess.TimeoutExpired:
        print(json.dumps({"transport_error": "command_timed_out", "check_retained_run": True}))
        break
    if reply.returncode == 0:
        print(json.dumps(json.loads(reply.stdout), separators=(",", ":")))
        break
    stderr = reply.stderr.decode(errors="replace").lower()
    reason = next((phrase for phrase in ("connection refused", "connection timed out") if phrase in stderr), None)
    if reply.returncode == 255 and reason is not None:
        failures.append(reason)
        if time.monotonic() < deadline:
            time.sleep(25)
            continue
        counts = {name: failures.count(name) for name in set(failures)}
        print(json.dumps({"transport_error": "ssh_unavailable", "connect_failures": counts}))
        break
    classes = re.findall(r"(?m)^([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception|Expired)):",
                         reply.stderr.decode(errors="replace"))
    print(json.dumps({"ssh_exit": reply.returncode, "remote_error_types": classes[-3:],
                      "stderr_bytes": len(reply.stderr), "check_retained_run": True}))
    break
