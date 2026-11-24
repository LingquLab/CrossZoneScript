"""Read evidence from the original same-server return."""
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

source = (directory/'local_evidence.py').read_text() + "\nimport json, struct\nfrom pathlib import Path\ncase = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/ht-shared-ready-t4-2e226468-local_return')\nprint(json.dumps(read_local_evidence(case), separators=(',', ':')))\n"
compile(source, "<local_phases>", "exec")
ssh = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
       "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1",
       "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no", HOST, "python3", "-"]
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=160)
if proc.returncode:
    print(json.dumps({"ssh_exit":proc.returncode,"stderr_bytes":len(proc.stderr)}))
else:
    print(json.dumps(json.loads(proc.stdout),separators=(",",":")))
