"""Run a pinned GLM operation through the authorized Windows-to-root jump route."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import shutil
import urllib.parse
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
assert args['operation'] in {'prepare', 'network', 'paths', 'runtime', 'download', 'compat', 'preflight', 'stop_download'}
remote = (Path(__file__).parent / (args['operation'] + '.py')).read_text()
proxies = {k: v for k, v in urllib.request.getproxies().items() if k in {'http', 'https'}}
proxy_url = proxies.get('https') or proxies.get('http')
assert proxy_url, 'bridge download proxy is required'
proxy = urllib.parse.urlsplit(proxy_url)
assert proxy.scheme in {'http', 'https'} and proxy.hostname and proxy.port
auth = proxy.netloc.rsplit('@', 1)[0] + '@' if '@' in proxy.netloc else ''
node_timeout = args.get('download_seconds', 86400) + 600 if args['operation'] == 'download' else 170
port = 18744 if args.get('host') == '141.61.94.26' else 18743
local_url = proxy._replace(netloc=auth + f'127.0.0.1:{port}').geturl()
node_source = 'ARGS = ' + repr(args) + '\nPROXIES = ' + repr({'http': local_url, 'https': local_url}) + '\n' + remote
needs_proxy = args['operation'] in {'prepare', 'network', 'download'}
forward = ['-o', 'ExitOnForwardFailure=yes', '-R', f'127.0.0.1:{port}:127.0.0.1:{port}'] if needs_proxy else []
jump_source = ('NODE_SOURCE = ' + repr(node_source) + '\nOPERATION = ' + repr(args['operation'])
               + '\nHOST = ' + repr(args.get('host', '141.61.94.58')) + '\nNODE_TIMEOUT = '
               + repr(node_timeout) + '\nFORWARD = ' + repr(forward) + '\n') + r'''
import json, subprocess
assert HOST in {'141.61.94.58','141.61.94.26'}
p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                    '-o', 'ServerAliveInterval=30', *FORWARD,
                    'root@'+HOST, 'python3', '-'], input=NODE_SOURCE,
                   capture_output=True, text=True, timeout=NODE_TIMEOUT)
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
forward = ['-o', 'ExitOnForwardFailure=yes', '-R', f'127.0.0.1:{port}:{proxy.hostname}:{proxy.port}'] if needs_proxy else []
p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', *forward,
                    '-o', 'ServerAliveInterval=30',
                    '-o', 'IdentitiesOnly=yes', '-i',
                    'C:/Users/l00929943/.ssh/id_ed25519_server',
                    'root@141.61.94.30', 'python3', '-'], input=jump_source,
                   capture_output=True, text=True, timeout=node_timeout+120)
if p.returncode:
    print(json.dumps({'stage': 'jump', 'exit': p.returncode,
                      'stderr_tail': p.stderr[-1400:]}))
    raise SystemExit(1)
result = json.loads(p.stdout)
result['bridge_hapi_cli'] = shutil.which('hapi')
if args['operation'] == 'network':
    probe = subprocess.run([sys.executable, str(Path(__file__).parent / 'network.py')],
                           capture_output=True, text=True, timeout=90)
    result['bridge_probe'] = json.loads(probe.stdout) if probe.returncode == 0 else {'exit': probe.returncode}
encoded = json.dumps(result)
assert len(encoded.encode()) < 62000
print(encoded)
if result.get('stage') in {'node', 'jump'} or result.get('error_class'):
    raise SystemExit(1)
