"""Stage both nodes, then parse the configured CLI using the installed runtime."""
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
with tempfile.TemporaryDirectory(prefix='glm16-stage-validate-') as directory:
    args = Path(directory) / 'args.json'
    args.write_text(json.dumps({'operation': 'stage'}))
    p = subprocess.run([sys.executable, str(here / 'preflight_both.py'), '--args-file', str(args)],
                       capture_output=True, text=True, timeout=160)
    assert p.returncode == 0, 'two_node_staging_failed'
    staged = json.loads(p.stdout)
    args.write_text(json.dumps({'operation': 'validate_cli', 'host': '141.61.94.26'}))
    p = subprocess.run([sys.executable, str(here / 'dispatch.py'), '--args-file', str(args)],
                       capture_output=True, text=True, timeout=350)
    assert p.returncode == 0, 'CLI_observation_failed'
    cli = json.loads(p.stdout)
    args.write_text(json.dumps({'operation': 'download_status', 'host': '141.61.94.58'}))
    p = subprocess.run([sys.executable, str(here / 'dispatch.py'), '--args-file', str(args)],
                       capture_output=True, text=True, timeout=50)
    download = json.loads(p.stdout) if p.returncode == 0 else {'query_exit': p.returncode}
    print(json.dumps({'staged': [x['host'] for x in staged], 'cli_check': cli, 'download': download}))
