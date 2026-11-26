"""Read the failed download receipt plus live download and coordinator state."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', required=True)
parser.parse_args()
here = Path(__file__).parent
out = {}
with tempfile.TemporaryDirectory(prefix='glm16-recovery-') as directory:
    p = Path(directory) / 'args.json'
    p.write_text(json.dumps({'actions': [{'operation': 'download_status', 'host': '141.61.94.58'}],
        'receipt_tasks': [{'task': 'glm16-download-20261125', 'iteration': 2},
                          {'task': 'glm16-pipeline-20261125', 'iteration': 1}]}))
    r = subprocess.run([sys.executable, str(here/'collect.py'), '--args-file', str(p)],
                       capture_output=True, text=True, timeout=150)
    out['download'] = json.loads(r.stdout) if r.stdout.strip() else {'query_exit': r.returncode}
    p.write_text('{}')
    r = subprocess.run([sys.executable, str(here/'pipeline_status.py'), '--args-file', str(p)],
                       capture_output=True, text=True, timeout=40)
    out['pipeline'] = json.loads(r.stdout) if r.stdout.strip() else {'query_exit': r.returncode}
print(json.dumps(out))
