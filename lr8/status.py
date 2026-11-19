"""Read LR8 case receipts and last-stage diagnostics without changing the run."""
import argparse
import json
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
label = args['label']
assert re.fullmatch('[a-z0-9-]{1,45}', label)
source = 'NAME = ' + repr('tilexr-lr8-0c04d831-' + label) + '\n'
source += r'''
import json, pathlib, re, subprocess
results = []
for root in sorted(pathlib.Path('/opt').glob(NAME + '-*'))[-1:]:
    container = subprocess.run(['docker', 'inspect', '--format', '{{.State.Status}}', NAME],
                               capture_output=True, text=True, timeout=12)
    summary = dict(container=container.stdout.strip(), cases=[])
    for receipt in sorted(root.glob('*i32-*/execution.json')):
        try:
            run = json.loads(receipt.read_text())
        except (OSError, ValueError):
            continue
        summary['cases'].append([receipt.parent.name, run.get('state'), run.get('simulated_us')])
    logs = sorted(root.glob('*.log'), key=lambda path: path.stat().st_mtime)
    if logs:
        latest = logs[-1]
        summary['latest_stage'] = latest.stem
        paths = [latest]
        if latest.stem.startswith('run-'):
            case = root/latest.stem.removeprefix('run-')
            paths.extend(sorted(case.glob('runs/*/sim.log'))[-1:])
        errors = []
        for path in paths:
            with path.open('rb') as log:
                log.seek(max(0, path.stat().st_size - 32768))
                lines = log.read().decode('utf-8', 'replace').splitlines()
            errors.extend(row.strip()[:320] for row in lines
                          if re.search(r'FATAL|exited with code|(?:Value|Runtime|Assertion|ModuleNotFound|FileNotFound)Error:|fatal error:', row))
        summary['errors'] = errors[-4:]
    results.append(summary)
print(json.dumps(results))
'''
result = subprocess.run(
    ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'IdentitiesOnly=yes',
     '-i', 'C:/Users/l00929943/.ssh/id_ed25519_server', 'root@141.61.94.30', 'python3 -'],
    input=source, text=True, capture_output=True, timeout=65)
print(json.dumps(dict(ssh_exit=result.returncode,
                      results=json.loads(result.stdout) if result.returncode == 0 else [])))
