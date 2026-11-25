"""Run an authorized independent operation on both nodes in one Bridge envelope."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', required=True)
operation = json.loads(Path(parser.parse_args().args_file).read_text()).get('operation', 'preflight')
assert operation in {'preflight', 'stage'}

def check(host):
    with tempfile.TemporaryDirectory(prefix='glm16-preflight-') as directory:
        args = Path(directory) / 'args.json'
        args.write_text(json.dumps({'operation': operation, 'host': host}))
        result = subprocess.run([sys.executable, str(Path(__file__).parent / 'dispatch.py'),
                                 '--args-file', str(args)], capture_output=True,
                                text=True, timeout=135)
        record = {'host': host, 'exit': result.returncode}
        try:
            record['evidence'] = json.loads(result.stdout)
        except ValueError:
            record['stdout_bytes'] = len(result.stdout)
        return record

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    records = list(pool.map(check, ['141.61.94.58', '141.61.94.26']))
print(json.dumps(records))
if any(x['exit'] for x in records):
    raise SystemExit(1)
