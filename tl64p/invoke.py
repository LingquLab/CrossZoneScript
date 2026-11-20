import argparse
import json
from pathlib import Path
import subprocess
import sys

cli = argparse.ArgumentParser()
cli.add_argument('--args-file', required=True, type=Path)
args = json.loads(cli.parse_args().args_file.read_bytes())
here = Path(__file__).resolve().parent
operation = args['operation']
remote_command = 'docker exec -i simbox python3 -B -'
if operation == 'build':
    manifest = json.loads((here/'sha.json').read_bytes())
    resources = {'manifest': manifest, 'archive': (here/'sources.tar.gz').read_bytes(),
                 'compile_source': (here/'compile.sh').read_bytes()}
    seconds = 440
elif operation == 'stage':
    resources = {}
    seconds = 145
elif operation == 'run':
    resources = {}
    seconds = 4020
elif operation == 'report':
    resources = {}
    seconds = 225
elif operation == 'status':
    resources = {}
    seconds = 60
elif operation == 'debug':
    name = b'dispatch_tilelang_64p_top6_68d32608_run01'
    debug = b'dispatch_tilelang_64p_top6_68d32608_debug01'
    resources = {key + '_source': (here/f'{key}_remote.py').read_bytes().replace(name,debug)
                 for key in ('stage','run')}
    resources['run_source'] = resources['run_source'].replace(b'3600s',b'200s').replace(b'3640)',b'235)')
    remote_command = 'python3 -B -'
    seconds = 575
else:
    raise ValueError('unknown operation')
remote = ''.join(f'{name} = {value!r}\n' for name,value in resources.items()).encode()
remote += (here/f'{operation}_remote.py').read_bytes()
compile(remote,operation,'exec')
argv=['ssh','-i',str(Path.home()/'.ssh/deploy_ed25519'),'-o','IdentitiesOnly=yes',
      '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12',
      'root@141.61.94.18',remote_command]
p = subprocess.run(argv, input=remote, capture_output=True, timeout=seconds)
(Path.cwd()/f'tl64p-{operation}.stdout.log').write_bytes(p.stdout)
(Path.cwd()/f'tl64p-{operation}.stderr.log').write_bytes(p.stderr)
if p.returncode:
    result={'status':'REMOTE_FAILURE','operation':operation,'ssh_exit':p.returncode}
else:
    result=json.loads(p.stdout)
print(json.dumps(result,ensure_ascii=True,separators=(',',':')))
sys.exit(0 if result['status']=='PASS' else 1)
