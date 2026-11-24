"""Submit the four dependent SST phases on the retained platform."""
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

options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ['--args-file'] else {}
support = root / 'ht_t32_e2e'
request = dict(plans=base64.b64encode((here / 'plans.zip').read_bytes()).decode(),
               sender=base64.b64encode((root / 'ht_t4_send_ab/opt/inter_server_send.o.gz').read_bytes()).decode(),
               exports=(support / 'exports.py').read_text(), checker=(here / 'check.py').read_text())
if options.get('guarded_local') is True:
    request['guarded_local'] = base64.b64encode((here / 'local-guarded.o.gz').read_bytes()).decode()
base = (root / 'inter_server/run_mte_t4_remote.py').read_text().rsplit('if __name__ == ', 1)[0]
prepare = (support / 'prepare.py').read_text().replace('(4, 16, 32)', '(64,)').replace(
    '"tilexr_moonep_combine_ht_kernel"}',
    '"tilexr_moonep_combine_ht_kernel", "tilexr_ep_ht_inter_server_send_kernel", "tilexr_ep_ht_final_reduce_sst_kernel"}'
).replace('assert build["compiled_in"] == "blue"', 'assert build["compiled_in"] == "retained"')
common = (root / 'ht_four_stage/local_phases.py').read_text().replace('"--timeout", "540"', '"--timeout", "1800"').replace(
    'timeout=600)', 'timeout=1860)')
source = '\n'.join((base, prepare, common, (here / 'routes.py').read_text(),
                    (here / 'phases.py').read_text(), f'''
try:
    print(json.dumps(execute_request({request!r}), separators=(',', ':')))
except Exception as error:
    import traceback
    frame = traceback.extract_tb(error.__traceback__)[-1]
    print(json.dumps(dict(error_type=type(error).__name__, function=frame.name, line=frame.lineno)))
'''))
compile(source, '<ht-bs64-random>', 'exec')
ssh = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
       '-o', 'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1', '-o', 'PreferredAuthentications=password',
       '-o', 'PubkeyAuthentication=no', HOST, 'python3', '-']
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=7500)
if proc.returncode:
    print(json.dumps(dict(ssh_exit=proc.returncode, stderr_bytes=len(proc.stderr))))
else:
    print(json.dumps(json.loads(proc.stdout), separators=(',', ':')))
