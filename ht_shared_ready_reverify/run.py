"""Submit the pinned T4 revalidation on the Green SST platform."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parent
shared = root/'ht_shared_ready'
support = root/'ht_t32_e2e'
sys.path[:0] = [str(support), str(root/'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST

manifest = json.loads((here/'manifest.json').read_text())
hashes = manifest['object_sha256']


def encoded(path, name):
    data = path.read_bytes()
    assert hashlib.sha256(gzip.decompress(data)).hexdigest() == hashes[name]
    return base64.b64encode(data).decode()


abis = base64.b64encode((shared/'abis.json.gz').read_bytes()).decode()
exports = (support/'exports.py').read_text()
attached = dict(object=encoded(shared/'local.o.gz', 'attached'), sha256=hashes['attached'],
                abis=abis, exports=exports,
                checker=(root/'ht_four_stage/check_local.py').read_text().replace(
                    'if phase == 1 else b"\\xa5" * 512',
                    'if phase == 1 else b"\\xa5" * 32 + bytes(32) + b"\\xa5" * 448'))
stages = ('inter_server_send','local_return','final_reduce')
returns = dict(objects={name:encoded(shared/(name+'.o.gz'),name) for name in stages},
               hashes={name:hashes[name] for name in stages}, abis=abis,
               exports=exports, loader=(shared/'loader.py').read_text(),
               checker=(root/'ht_four_stage/check_remote.py').read_text())
fixed = encoded(here/'local_return_fixed.o.gz','local_return_fixed')
base = (root/'inter_server/run_mte_t4_remote.py').read_text().rsplit('if __name__ == ', 1)[0]
prepare = (support/'prepare.py').read_text().replace(
    '"tilexr_moonep_combine_ht_kernel"}',
    '"tilexr_moonep_combine_ht_attached", "tilexr_ep_ht_inter_server_send_kernel", '
    '"tilexr_ep_ht_local_return_kernel", "tilexr_ep_ht_final_reduce_sst_kernel"}'
).replace('chip["block_dim"] == 16', 'chip["block_dim"] in (8, 16)')
source = '\n'.join((base, prepare, (root/'ht_four_stage/local_phases.py').read_text(),
                    *((shared/name).read_text() for name in ('handoff.py','returns.py','attached.py',
                                                            'local_evidence.py','downstream.py')),
                    (here/'verify.py').read_text(), f'''
try:
    print(json.dumps(reverify({manifest!r}, {attached!r}, {returns!r}, {fixed!r}), separators=(',', ':')))
except Exception as error:
    import traceback
    frame = traceback.extract_tb(error.__traceback__)[-1]
    print(json.dumps(dict(error_type=type(error).__name__, function=frame.name, line=frame.lineno)))
'''))
compile(source, '<ht-reverify>', 'exec')
ssh = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '-o',
       'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1', '-o', 'PreferredAuthentications=password',
       '-o', 'PubkeyAuthentication=no', HOST, 'python3', '-']
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=3200)
if proc.returncode:
    print(json.dumps(dict(ssh_exit=proc.returncode, stderr_bytes=len(proc.stderr))))
else:
    print(json.dumps(json.loads(proc.stdout), separators=(',', ':')))
