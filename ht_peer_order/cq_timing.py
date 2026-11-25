"""Read the completion timestamp distributions for the six successful BS128 cases."""
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
import re

root = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
stamp = re.compile(rb'\bSim:\s*(\d+)')
results = {}
for seed in (20261124, 20261125, 20261126):
    for mode in ('rotate', 'peer'):
        version = 'v1' if seed == 20261124 and mode == 'peer' else 'v2'
        case = root / f'ht-peer-order-{version}-t128-{seed}-{mode}'
        result_file = case / 'benchmark-result.json'
        log = case / 'sim.log'
        key = f'{seed}-{mode}'
        if not result_file.is_file() or not log.is_file():
            results[key] = {'present': False}
            continue
        result = json.loads(result_file.read_text())
        assert result['status'] == 'sst_and_checker_passed'
        times = []
        with log.open('rb') as stream:
            for line in stream:
                if b'WrCQEResp' in line:
                    match = stamp.search(line)
                    if match:
                        times.extend([int(match.group(1))] * line.count(b'WrCQEResp'))
        times.sort()
        assert len(times) == result['expected_cqe'] == result['cqe']
        fields = dict(total_sim_us=result['sim_us'], cqe=len(times))
        fields['ps_first_p50_p90_p95_p99_last'] = [
            times[(len(times) - 1) * q // 100] for q in (0, 50, 90, 95, 99, 100)
        ]
        fields['ba_files'] = len(list(case.glob('OUTPUT/stat_trace/Host*_ba_layer_trace.json')))
        results[key] = fields
print(json.dumps(results, separators=(',', ':')))
'''

ssh = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
       '-o', 'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1', '-o',
       'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no', HOST, 'python3', '-']
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=85)
if proc.returncode:
    print(json.dumps(dict(ssh_exit=proc.returncode, stderr_bytes=len(proc.stderr))))
else:
    print(json.dumps(json.loads(proc.stdout), separators=(',', ':')))
