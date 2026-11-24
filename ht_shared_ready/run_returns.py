"""Run the return stages from the completed attached SST reducer."""
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
assert sys.argv[1:2] == ['--args-file']
options = json.loads(Path(sys.argv[2]).read_text())
stages = options['stages']
assert len(stages) == len(set(stages)) and stages
assert set(stages) <= {'inter_server_send', 'local_return', 'final_reduce'}
scratch128 = bool(options.get('scratch128'))
assert not (scratch128 and options.get('packed24'))
images = {name:(directory/(name+'.o.gz')).read_bytes() for name in stages}
if scratch128 and 'local_return' in images:
    images['local_return'] = (directory/'local_return_scratch128.o.gz').read_bytes()
    assert hashlib.sha256(gzip.decompress(images['local_return'])).hexdigest() == (
        'f74fa81c07b73ed9fa0b9334a99651ccbaa193f26cd2a6c5825ba042ddc279dd')
request = dict(stages=stages, packed24=bool(options.get('packed24')), scratch128=scratch128, objects={name:base64.b64encode(image).decode() for name,image in images.items()},
               hashes={name:hashlib.sha256(gzip.decompress(image)).hexdigest() for name,image in images.items()},
               abis=base64.b64encode((directory/'abis.json.gz').read_bytes()).decode(),
               checker=(root/'ht_four_stage'/'check_remote.py').read_text(),
               exports=(support/'exports.py').read_text(), loader=(directory/'loader.py').read_text())
base = (root / "inter_server/run_mte_t4_remote.py").read_text().rsplit('if __name__ == ', 1)[0]
prepare = (support/'prepare.py').read_text().replace(
    '"tilexr_moonep_combine_ht_kernel"}',
    '"tilexr_ep_ht_inter_server_send_kernel", "tilexr_ep_ht_local_return_kernel", "tilexr_ep_ht_final_reduce_sst_kernel"}'
).replace('chip["block_dim"] == 16', 'chip["block_dim"] in (8, 16)')
source = "\n".join((base, prepare, (root/'ht_four_stage'/'local_phases.py').read_text(),
                    (directory/'returns.py').read_text(), (directory/'attached.py').read_text(),
                    (directory/'local_evidence.py').read_text(), (directory/'downstream.py').read_text(), f"""
try:
    print(json.dumps(run_downstream({request!r}), separators=(',', ':')))
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
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=650*len(stages))
if proc.returncode:
    print(json.dumps({"ssh_exit":proc.returncode,"stderr_bytes":len(proc.stderr)}))
else:
    print(json.dumps(json.loads(proc.stdout),separators=(",",":")))
