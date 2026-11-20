"""Read bounded UniTest errors and owned-container process state; never rerun."""
import json
import os
from pathlib import Path
import re
import subprocess


report = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr/reports/lr8-2'
result = {'report': str(report)}
for name in ['unitest-progress.json', 'singlepoint-results.json']:
    path = report / name
    if path.is_file():
        result[name] = json.loads(path.read_text(encoding='utf-8'))
result['cases'] = []
for elements in [129, 128]:
    label = 'lr8-8p-' + str(elements * 4) + 'b'
    output = report / ('unitest-' + label)
    case = {'case': label, 'output_exists': output.exists(), 'diagnostics': {}}
    path = output / 'result.json'
    if path.is_file():
        data = json.loads(path.read_text(encoding='utf-8'))
        case['status'] = data.get('status')
        case['error'] = str(data.get('error', ''))[:900]
        case['phases'] = {name: {'exit_code': value.get('exit_code')} for name, value in data.get('phases', {}).items()}
    logs = [report / (label + '.controller.log'), output / 'prepare.log', output / 'run.log']
    for log in logs:
        if not log.is_file():
            continue
        lines = log.read_text(encoding='utf-8', errors='replace').splitlines()
        selected = [line for line in lines if len(line) <= 700 and re.search(
            r'(^[\w.]+(?:Error|Exception):|[Ee]rror:|FATAL|No such file|not found|Permission denied|failed|timed out|mismatch)', line)]
        if selected:
            case['diagnostics'][log.name] = selected[-6:]
    result['cases'].append(case)
p = subprocess.run(['ssh', '-T', '-i', 'C:/Users/z00813871/.ssh/id_ed25519',
    '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ConnectTimeout=12', 'root@141.61.94.26',
    'timeout 15 docker top tilexr-matrix-acc8c509-lr8-1 -eo pid,comm'],
    capture_output=True, timeout=30)
if p.returncode:
    raise RuntimeError('Owned-container process inspection failed')
result['container_processes'] = p.stdout.decode('utf-8', errors='replace').splitlines()[:40]
print(json.dumps(result, ensure_ascii=True))
