"""Keep the scoped two-node workflow in the foreground through the jump host."""
import argparse
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text())
assert isinstance(args['wait_seconds'], int) and 60 <= args['wait_seconds'] <= 172800
source = 'ARGS = ' + repr(args) + '\n' + (Path(__file__).parent / 'pipeline_remote.py').read_text()
p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
    '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=20', '-o', 'IdentitiesOnly=yes', '-i',
    'C:/Users/l00929943/.ssh/id_ed25519_server', 'root@141.61.94.30', 'python3', '-'],
    input=source, capture_output=True, text=True, timeout=args['wait_seconds']+6000)
if p.stdout.strip():
    data = json.loads(p.stdout)
    print(json.dumps(data))
else:
    print(json.dumps({'ssh_exit': p.returncode, 'stdout_bytes': len(p.stdout), 'stderr_bytes': len(p.stderr)}))
raise SystemExit(p.returncode)
