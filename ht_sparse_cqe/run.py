"""Run an isolated T4 window SO completion probe using retained real partials."""
import base64
import json
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parent
sys.path[:0] = [str(root / 'ht_t32_e2e'), str(root / 'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST

support = root / 'ht_t32_e2e'
previous = root / 'ht_four_stage'
request = dict(abis=json.loads((previous / 'abi.json').read_text()),
               checker=(previous / 'check_remote.py').read_text(), exports=(support / 'exports.py').read_text(),
               obj=base64.b64encode((here / 'sparse-cqe.o.gz').read_bytes()).decode())
base = (root / 'inter_server/run_mte_t4_remote.py').read_text().rsplit('if __name__ == ', 1)[0]
prepare = (support / 'prepare.py').read_text().replace('"tilexr_moonep_combine_ht_kernel"}',
    '"tilexr_moonep_combine_ht_kernel", "tilexr_ep_ht_inter_server_send_kernel"}')
phases = (previous / 'remote_phases.py').read_text().rsplit('def run_remote_phases(', 1)[0]
phases = phases.replace('compiled_in="green"', 'compiled_in="blue"')
source = '\n'.join((base, prepare, (previous / 'local_phases.py').read_text(), phases, f'request = {request!r}', r'''
import tempfile
try:
    case = MOUNT / 'ht-t4-inter-send-sparse-cqe-50066aab'
    if case.exists():
        report = summarize(case)
    else:
        with tempfile.TemporaryDirectory(prefix='ht-sparse-cqe-', dir=MOUNT.parent) as temp:
            temp = Path(temp)
            obj = temp / 'kernel.o'
            obj.write_bytes(gzip.decompress(base64.b64decode(request['obj'], validate=True)))
            assert sha(obj) == '50066aabb60fc853e6ff8a424065d741a3f4ec8077cdb5e3ea7354d49ffb5598'
            prepare_remote_phase(request, case, EP_SEND, obj, temp / 'golden', temp / 'loader', True)
        report = execute_phase(case)
    print(json.dumps(report, separators=(',', ':')))
except Exception as error:
    import traceback
    frame = traceback.extract_tb(error.__traceback__)[-1]
    print(json.dumps(dict(error_type=type(error).__name__, function=frame.name, line=frame.lineno)))
'''))
compile(source, '<ht-sparse-cqe>', 'exec')
ssh = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '-o',
       'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1', '-o', 'PreferredAuthentications=password',
       '-o', 'PubkeyAuthentication=no', HOST, 'python3', '-']
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=660)
if proc.returncode:
    print(json.dumps(dict(ssh_exit=proc.returncode, stderr_bytes=len(proc.stderr))))
else:
    print(json.dumps(json.loads(proc.stdout), separators=(',', ':')))
