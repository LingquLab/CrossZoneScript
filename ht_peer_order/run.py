"""Dispatch a bounded range of paired SST trials and return their checked summaries."""
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

assert sys.argv[1:2] == ['--args-file']
options = json.loads(Path(sys.argv[2]).read_text())
request = {key: int(options[key]) for key in ('tokens', 'start', 'stop')}
request.update(compact=bool(options.get('compact')), object=base64.b64encode((here/'kernel.o.gz').read_bytes()).decode(),
               abis=base64.b64encode((here/'abis.json.gz').read_bytes()).decode(),
               object_sha256=(here/'kernel.sha256').read_text().strip(),
               checker=(root/'ht_four_stage'/'check_remote.py').read_text(),
               exports=(root/'ht_t32_e2e'/'exports.py').read_text())
base = (root/'inter_server'/'run_mte_t4_remote.py').read_text().rsplit('if __name__ == ', 1)[0]
prepare = (root/'ht_t32_e2e'/'prepare.py').read_text().replace(
    '(4, 16, 32)', '(32, 64)').replace('"tilexr_moonep_combine_ht_kernel"}',
    '"tilexr_moonep_combine_ht_kernel", "tilexr_ep_ht_inter_server_send_kernel"}')
common = (root/'ht_four_stage'/'local_phases.py').read_text().replace('"--timeout", "540"', '"--timeout", "720"').replace(
    'timeout=600)', 'timeout=780)')
source = '\n'.join((base, prepare, common, (here/'fixture.py').read_text(),
                    (here/'phases.py').read_text().replace(
                        'from fixture import BASE, LOCAL, MAGIC, ROW_BYTES, TOPK, EXPERTS, WORLD, generate_inputs', ''), f'''
try:
    print(json.dumps(execute_benchmark({request!r}), separators=(',', ':')))
except Exception as error:
    import traceback
    frame = traceback.extract_tb(error.__traceback__)[-1]
    print(json.dumps(dict(error_type=type(error).__name__, function=frame.name, line=frame.lineno)))
'''))
compile(source, '<ht-peer-order>', 'exec')
ssh = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '-o',
       'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1', '-o', 'PreferredAuthentications=password',
       '-o', 'PubkeyAuthentication=no', HOST, 'python3', '-']
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True,
                          timeout=1700*(request['stop']-request['start']))
if proc.returncode:
    print(json.dumps(dict(ssh_exit=proc.returncode, stderr_bytes=len(proc.stderr))))
else:
    print(json.dumps(json.loads(proc.stdout), separators=(',', ':')))
