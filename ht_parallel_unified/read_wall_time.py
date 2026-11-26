"""Read retained timing metadata for the two passing BS32 SST runs."""

import json
from pathlib import Path
import subprocess
import sys

from config import T32_FP32_INIT_CASE, T32_PROFILE_CASE


root = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(root / 'ht_t32_e2e'), str(root / 'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST


cases = {'plain': T32_FP32_INIT_CASE, 'profile': T32_PROFILE_CASE}
source = f'cases = {cases!r}\n' + r'''
import json
from pathlib import Path
import re
import subprocess


root = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
wall_pattern = re.compile(
    r'(?:wall[- ]?clock(?: time)?|(?:total )?real time|elapsed wall time)\s*[:=]\s*'
    r'([0-9.]+)\s*(seconds?|secs?|s)\b', re.IGNORECASE)


def birth_time(path):
    value = subprocess.check_output(['stat', '-c', '%W', '--', str(path)], text=True)
    return int(value) or None


def read_timing(case):
    if not case.is_dir():
        return {'status': 'case_missing'}
    result = json.loads((case / 'unified-result.json').read_text())
    report = {'status': result['status'], 'kernel_sha256': result['kernel_sha256']}
    for name, key in [('sim.log', 'sst_log'), ('native-runner.log', 'runner_log'),
                      ('check.log', 'checker_log'), ('run.json', 'run_record'),
                      ('phase-summary.json', 'phase_summary')]:
        path = case / name
        if path.is_file():
            stats = path.stat()
            report[key] = {'created_s': birth_time(path), 'last_write_s': round(stats.st_mtime, 3)}
    with (case / 'sim.log').open('rb') as stream:
        stream.seek(max(0, (case / 'sim.log').stat().st_size - 65536))
        tail = stream.read().decode(errors='replace')
    report['wall_seconds_in_sst_log_tail'] = [float(match[0]) for match in wall_pattern.findall(tail)][-8:]
    report['has_sst_completion_line'] = 'simulation is complete' in tail.lower()
    return report


print(json.dumps({name: read_timing(root / case) for name, case in cases.items()}, separators=(',', ':')))
'''
compile(source, '<unified-wall-time>', 'exec')

ssh = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
       '-o', 'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1', '-o',
       'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no', HOST, 'python3', '-']
with askpass_environment() as env:
    result = subprocess.run(ssh, input=source.encode(), env=env, capture_output=True, timeout=85)
if result.returncode:
    print(json.dumps({'ssh_exit': result.returncode, 'stderr_bytes': len(result.stderr)}))
else:
    print(json.dumps(json.loads(result.stdout), separators=(',', ':')))
