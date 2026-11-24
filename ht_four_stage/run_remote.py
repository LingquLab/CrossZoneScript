"""Invoke the last two HT phases from the first two verified SST cases."""
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

args = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
support = root / "ht_t32_e2e"
request = dict(abis=json.loads((directory / "abi.json").read_text()),
               checker=(directory / "check_remote.py").read_text(),
               exports=(support / "exports.py").read_text(),
               compatible=args.get("compatible") is True)
base = (root / "inter_server/run_mte_t4_remote.py").read_text().rsplit('if __name__ == ', 1)[0]
prepare = (support / "prepare.py").read_text().replace(
    '"tilexr_moonep_combine_ht_kernel"}',
    '"tilexr_moonep_combine_ht_kernel", "tilexr_ep_ht_inter_server_send_kernel", "tilexr_ep_ht_final_reduce_sst_kernel"}'
).replace('assert build["compiled_in"] == "blue"', 'assert build["compiled_in"] == "green"')
extra = ""
if request["compatible"]:
    request["mte_sender"] = base64.b64encode(
        (root / "ht_t4_send_ab/opt/inter_server_send.o.gz").read_bytes()).decode()
    extra = (directory / "compatible_sender.py").read_text()
source = "\n".join((base, prepare, (directory / "local_phases.py").read_text(),
                    (directory / "remote_phases.py").read_text(), extra, f"""
try:
    print(json.dumps(run_remote_phases({request!r}), separators=(',', ':')))
except Exception as error:
    print(json.dumps({{'error_type': type(error).__name__}}))
"""))
compile(source, "<remote_phases>", "exec")
ssh = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
       "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1",
       "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no", HOST, "python3", "-"]
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=1490)
if proc.returncode:
    print(json.dumps({"ssh_exit":proc.returncode,"stderr_bytes":len(proc.stderr)}))
else:
    print(json.dumps(json.loads(proc.stdout),separators=(",",":")))
