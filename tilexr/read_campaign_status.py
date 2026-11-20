"""Read completed points and live process names without changing active runs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time


root = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr/reports'


def read(path):
    if not path.is_file():
        return None
    for attempt in range(3):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            if attempt == 2:
                return {'read_state': 'concurrent_write'}
            time.sleep(0.1)


result = {'observed_utc': datetime.now(timezone.utc).isoformat(), 'campaigns': [], 'verified_point_count': 0}
for name in ['profiled-8p-small-1', 'profiled-8p-512k-1', 'profiled-16p-32p-small-1',
             'profiled-8p-4m-1', 'profiled-16p-32p-512k-1']:
    directory = root / name
    rows = read(directory / 'results.json')
    completed = [{k: entry[k] for k in ['kernel', 'ranks', 'elements_per_peer', 'status', 'simulated_us',
                                       'reference_us', 'difference_us']} for entry in rows] if isinstance(rows, list) else []
    result['verified_point_count'] += sum(row['status'] == 'passed' for row in completed)
    result['campaigns'].append({'campaign': name, 'progress': read(directory / 'progress.json'), 'completed_points': completed})
p = subprocess.run(['ssh', '-T', '-i', 'C:/Users/z00813871/.ssh/id_ed25519',
    '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ConnectTimeout=12', 'root@141.61.94.26',
    'timeout 15 docker top tilexr-matrix-acc8c509-lr8-1 -eo pid,etimes,comm'], capture_output=True, timeout=30)
if p.returncode:
    raise RuntimeError('Read-only process status query failed')
result['container_processes'] = p.stdout.decode(errors='replace').splitlines()[:30]
print(json.dumps(result, ensure_ascii=True))
