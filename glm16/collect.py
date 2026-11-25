"""Collect task-scoped diagnostics, bounded device probes and saved receipts."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text())
actions = args.get('actions', [])
assert len(actions) <= 3
assert all(x['operation'] in {'probe_logs', 'download_status', 'preflight', 'proxy_probe', 'npu_probe'}
           and x['host'] in {'141.61.94.58', '141.61.94.26'} for x in actions)

def collect(action):
    with tempfile.TemporaryDirectory(prefix='glm16-collect-') as directory:
        p = Path(directory) / 'args.json'
        p.write_text(json.dumps(action))
        result = subprocess.run([sys.executable, str(Path(__file__).parent / 'dispatch.py'),
                                 '--args-file', str(p)], capture_output=True, text=True,
                                timeout=300 if action['operation'] == 'npu_probe' else 135)
        row = dict(action, exit=result.returncode)
        try:
            row['evidence'] = json.loads(result.stdout)
        except ValueError:
            row['stdout_bytes'] = len(result.stdout)
        return row

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    records = list(pool.map(collect, actions))
out = {'diagnostics': records, 'receipts': []}
for item in args.get('receipt_tasks', []):
    assert re.fullmatch(r'glm16-[a-z0-9-]+', item['task'])
    assert isinstance(item['iteration'], int) and item['iteration'] > 0
    p = Path.cwd() / 'runs' / item['task'] / str(item['iteration']) / 'execution'
    row = dict(item, execution_file_exists=(p / 'execution.json').is_file())
    if (p / 'execution.json').is_file():
        row['execution'] = json.loads((p / 'execution.json').read_text())
    if (p / 'stdout.log').is_file():
        try:
            d = json.loads((p / 'stdout.log').read_text())
            row['result'] = {k: d[k] for k in ['cdn_host', 'requests', 'stage', 'exit', 'error_class'] if k in d}
        except ValueError:
            row['stdout_bytes'] = (p / 'stdout.log').stat().st_size
    if (p / 'stderr.log').is_file():
        t = (p / 'stderr.log').read_text(errors='replace')
        row['exception_types'] = re.findall(r'\b(?:HTTPError|URLError|TimeoutExpired|FileNotFoundError|AssertionError|JSONDecodeError)\b', t)[-5:]
    out['receipts'].append(row)
print(json.dumps(out))
if any(x['exit'] for x in records):
    raise SystemExit(1)
