"""Read existing matrix qualification status and selected compiler/tool errors."""
import json
import os
from pathlib import Path
import re
import subprocess


root = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr/reports/matrix-build-1'
if root.with_name('matrix-build-2').is_dir():
    root = root.with_name('matrix-build-2')
result = {'report': str(root), 'report_exists': root.is_dir()}
for name in ['progress.json', 'summary.json', 'asset-discovery.json']:
    path = root / name
    if path.is_file():
        result[name] = json.loads(path.read_text(encoding='utf-8'))
result['diagnostics'] = {}
for path in sorted(root.glob('*/*.log')):
    matches = []
    with path.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            if len(line) < 1100 and re.search(r'error:|Error:|ERROR|failed|No such|not found|Permission denied|mismatch|timed out', line):
                matches.append(line.rstrip())
    if matches:
        result['diagnostics'][str(path.relative_to(root))] = matches[-6:]
result['phase_states'] = {}
for path in sorted(root.glob('*/*/result.json')):
    result['phase_states'][str(path.relative_to(root))] = json.loads(path.read_text(encoding='utf-8')).get('state')
p = subprocess.run(['ssh', '-T', '-i', 'C:/Users/z00813871/.ssh/id_ed25519',
    '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ConnectTimeout=12', 'root@141.61.94.26',
    'timeout 15 docker top tilexr-matrix-acc8c509-lr8-1 -eo pid,comm'], capture_output=True, timeout=30)
if p.returncode:
    raise RuntimeError('Container process-state inspection failed')
result['container_processes'] = p.stdout.decode(errors='replace').splitlines()[:30]
print(json.dumps(result, ensure_ascii=True))
