"""Transfer the pinned T32 fused FP32 workload when the green platform recovers."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from contextlib import nullcontext

from green_auth import askpass_environment

root = Path(__file__).resolve().parent
args = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
object_name = "inter_server_send.o.gz"
identity_name = "fixture-identity.json"
fixture_name = "fixture-images.tar.gz"
if args.get("sq_stripe") is True:
    object_name = "sq-stripe-a7286b7a.o.gz"
    identity_name = "sq-stripe-identity.json"
elif args.get("small_t4") is True:
    identity_name = "t4-identity.json"
    fixture_name = "t4-fixture-images.tar.gz"
elif args.get("spread_gateways") is True:
    identity_name = "t32-spread-identity.json"
    fixture_name = "t32-spread-fixture-delta.tar.gz"
identity = json.loads((root / identity_name).read_text())
fixture = (root / fixture_name).read_bytes()
object_package = (root / object_name).read_bytes()
delta = args.get("spread_gateways") is True
digest_field = "delta_archive_sha256" if delta else "input_archive_sha256"
assert hashlib.sha256(fixture).hexdigest() == identity[digest_field]
assert hashlib.sha256(gzip.decompress(object_package)).hexdigest() == identity["kernel_sha256"]
entry = identity["entry"]
assert entry == "tilexr_ep_ht_combine_sst_fp32"
build = dict(entry=entry, variant="sst-mte-fused-fp32", candidate_commit=identity["commit"],
             kernel_sha256=identity["kernel_sha256"], compiled_in="blue")
request = dict(build=build, identity=identity, archive=base64.b64encode(fixture).decode(),
               object=base64.b64encode(object_package).decode(), checker=(root/"check.py").read_text(),
               exports=(root/"exports.py").read_text(), spread_gateways=delta)
if delta:
    manifest = (root / "t32-spread-delta-manifest.json").read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == identity["delta_manifest_sha256"]
    request["delta_manifest"] = base64.b64encode(manifest).decode()
runner = (root.parent/"inter_server/run_mte_t4_remote.py").read_text().rsplit("if __name__ == ", 1)[0]
assert runner.count("assert tokens == 4") == 1
runner = runner.replace("assert tokens == 4", "assert tokens in (4, 32)")
source = "\n".join((runner, (root/"prepare.py").read_text(), (root/"remote.py").read_text(),
                    f"print(json.dumps(run_e2e({request!r}), separators=(',', ':')))" ))
compile(source, "<t32_fused_remote>", "exec")
password_auth = args.get("local_auth_note") is True
ssh = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8"]
if password_auth:
    ssh.extend(["-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1",
                "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no"])
else:
    ssh.extend(["-o", "BatchMode=yes"])
ssh.extend(["root@141.61.94.58", "python3", "-"])


def run(environment):
    deadline = time.monotonic() + 540
    failures = []
    while True:
        try:
            reply = subprocess.run(ssh, input=source.encode(), env=environment,
                                   capture_output=True, timeout=2200)
        except subprocess.TimeoutExpired:
            return {"transport_error": "command_timed_out", "check_retained_run": True}
        if reply.returncode == 0:
            try:
                return json.loads(reply.stdout)
            except ValueError:
                return {"transport_error": "invalid_receipt", "check_retained_run": True}
        stderr = reply.stderr.decode(errors="replace").lower()
        if reply.returncode == 255 and "permission denied" in stderr:
            return {"transport_error": "authentication_denied"}
        reason = next((phrase for phrase in ("connection refused", "connection timed out") if phrase in stderr), None)
        if reply.returncode == 255 and reason is not None:
            failures.append(reason)
            if time.monotonic() < deadline:
                time.sleep(25)
                continue
            return {"transport_error": "ssh_unavailable",
                    "connect_failures": {name: failures.count(name) for name in set(failures)}}
        classes = re.findall(r"(?m)^([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception|Expired)):",
                             reply.stderr.decode(errors="replace"))
        return {"ssh_exit": reply.returncode, "remote_error_types": classes[-3:],
                "stderr_bytes": len(reply.stderr), "check_retained_run": True}


try:
    with askpass_environment() if password_auth else nullcontext(None) as environment:
        result = run(environment)
except FileNotFoundError:
    result = {"transport_error": "configured_local_auth_note_not_found"}
print(json.dumps(result, separators=(",", ":")))
