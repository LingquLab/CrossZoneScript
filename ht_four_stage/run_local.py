"""Invoke the retained SST platform with the first two HT phases."""
import base64
import json
from pathlib import Path
import subprocess
import sys

directory = Path(__file__).resolve().parent
root = directory.parent
sys.path[:0] = [str(root / "ht_t32_e2e"), str(root / "alltoall")]
from green_auth import askpass_environment
from ssh_transport import HOST

support = root / "ht_t32_e2e"
local = support / "local-ht"
request = dict(
    archive=base64.b64encode((local / "fixture-images.tar.gz").read_bytes()).decode(),
    identity=json.loads((local / "poll-mask-identity.json").read_text()),
    checker=(directory / "check_local.py").read_text(), exports=(support / "exports.py").read_text(),
    tokens=4, hidden=128,
)
base = (root / "inter_server/run_mte_t4_remote.py").read_text().rsplit('if __name__ == ', 1)[0]
source = "\n".join((base, (support / "prepare.py").read_text(),
                    (directory / "local_phases.py").read_text(), f"""
try:
    print(json.dumps(run_local_phases({request!r}), separators=(',', ':')))
except Exception as error:
    print(json.dumps({{'error_type': type(error).__name__}}))
"""))
compile(source, "<local_phases>", "exec")
ssh = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
       "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1",
       "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no", HOST, "python3", "-"]
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=1400)
if proc.returncode:
    print(json.dumps({"ssh_exit":proc.returncode,"stderr_bytes":len(proc.stderr)}))
else:
    print(json.dumps(json.loads(proc.stdout),separators=(",",":")))
