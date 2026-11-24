"""Read the bounded status of the send-only SST cases."""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(root/'ht_t32_e2e'), str(root/'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST

source = r'''
import json
from pathlib import Path
import time

root = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
cases = {}
for case in sorted(root.glob('ht-reverify-t4-3bfa9fe5-r1-*')):
    if not case.is_dir():
        continue
    result = case/'phase-summary.json'
    run = case/'run.json'
    log = case/'sim.log'
    if result.is_file():
        fields = json.loads(result.read_text())
        cases[case.name] = {k: fields[k] for k in ('status', 'cqe', 'sim_us', 'runner_exit_code') if k in fields}
    else:
        fields = dict(status=json.loads(run.read_text())['status'] if run.is_file() else 'preparing')
        if log.is_file():
            fields.update(cqe=log.read_bytes().count(b'WrCQEResp'),
                          silence_seconds=round(time.time() - log.stat().st_mtime))
        cases[case.name] = fields
print(json.dumps(cases, separators=(',', ':')))
'''
ssh = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '-o',
       'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1', '-o', 'PreferredAuthentications=password',
       '-o', 'PubkeyAuthentication=no', HOST, 'python3', '-']
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=85)
if proc.returncode:
    print(json.dumps(dict(ssh_exit=proc.returncode, stderr_bytes=len(proc.stderr))))
else:
    print(json.dumps(json.loads(proc.stdout), separators=(',', ':')))
