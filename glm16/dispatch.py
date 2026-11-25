"""Run a pinned GLM operation through the authorized Windows-to-root jump route."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
assert args['operation'] in {'prepare', 'network'}
remote = (Path(__file__).parent / (args['operation'] + '.py')).read_text()
node_source = 'ARGS = ' + repr(args) + '\n' + remote
jump_source = 'NODE_SOURCE = ' + repr(node_source) + '\nOPERATION = ' + repr(args['operation']) + '\n' + r'''
import json, subprocess
p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                    'root@141.61.94.58', 'python3', '-'], input=NODE_SOURCE,
                   capture_output=True, text=True, timeout=170)
if p.returncode:
    print(json.dumps({'stage': 'node', 'exit': p.returncode,
                      'stderr_tail': p.stderr[-1400:]}))
else:
    result = json.loads(p.stdout)
    if OPERATION == 'network':
        q = subprocess.run(['python3', '-'], input=NODE_SOURCE,
                           capture_output=True, text=True, timeout=100)
        result['jump_probe'] = json.loads(q.stdout) if q.returncode == 0 else {'exit': q.returncode}
    print(json.dumps(result))
'''
p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                    '-o', 'IdentitiesOnly=yes', '-i',
                    'C:/Users/l00929943/.ssh/id_ed25519_server',
                    'root@141.61.94.30', 'python3', '-'], input=jump_source,
                   capture_output=True, text=True, timeout=290)
if p.returncode:
    print(json.dumps({'stage': 'jump', 'exit': p.returncode,
                      'stderr_tail': p.stderr[-1400:]}))
    raise SystemExit(1)
result = json.loads(p.stdout)
if args['operation'] == 'network':
    probe = subprocess.run([sys.executable, str(Path(__file__).parent / 'network.py')],
                           capture_output=True, text=True, timeout=90)
    result['bridge_probe'] = json.loads(probe.stdout) if probe.returncode == 0 else {'exit': probe.returncode}
encoded = json.dumps(result)
assert len(encoded.encode()) < 62000
print(encoded)
if result.get('stage') in {'node', 'jump'} or result.get('error_class'):
    raise SystemExit(1)
