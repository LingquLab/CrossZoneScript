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
import gzip
import hashlib
object_image = (directory/'local.o.gz').read_bytes()
request = dict(object=base64.b64encode(object_image).decode(),
               abis=base64.b64encode((directory/'abis.json.gz').read_bytes()).decode(),
               sha256=hashlib.sha256(gzip.decompress(object_image)).hexdigest(),
               checker=(root/'ht_four_stage'/'check_local.py').read_text().replace(
                   'if phase == 1 else b"\\xa5" * 512',
                   'if phase == 1 else b"\\xa5" * 32 + bytes(32) + b"\\xa5" * 448'),
               exports=(support/'exports.py').read_text())
base = (root / "inter_server/run_mte_t4_remote.py").read_text().rsplit('if __name__ == ', 1)[0]
prepare = (support/'prepare.py').read_text().replace(
    '"tilexr_moonep_combine_ht_kernel"}', '"tilexr_moonep_combine_ht_attached"}')
source = "\n".join((base, prepare, (root/'ht_four_stage'/'local_phases.py').read_text(),
                    (directory/'handoff.py').read_text(), (directory/'attached.py').read_text(), f"""
try:
    print(json.dumps(attached_case({request!r}), separators=(',', ':')))
except Exception as error:
    import traceback
    frame = traceback.extract_tb(error.__traceback__)[-1]
    print(json.dumps(dict(error_type=type(error).__name__, function=frame.name, line=frame.lineno)))
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
