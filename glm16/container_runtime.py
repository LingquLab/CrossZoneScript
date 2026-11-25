"""Create and inspect only the containers owned by this GLM test."""
import argparse
import glob
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path('/home/glm16-09b20820')
SPEC = json.loads((ROOT / 'launch-spec.json').read_text())
IMAGE = 'sha256:5588559036095d339c1cfb48c16d0227b841355490d8c0cb4e6730759f9c6674'
assert SPEC['image'] == IMAGE and SPEC['model'] == '/mnt/share/GLM-5.2'

def idle():
    p = subprocess.run(['/usr/local/sbin/npu-smi', 'info'], capture_output=True, text=True, timeout=65)
    table = p.stdout.split('Process id', 1)[-1]
    rows = [x.strip() for x in table.splitlines() if re.match(r'^\|\s*\d+\s*\|\s*\d+\s*\|', x)]
    return {'idle': p.returncode == 0 and 'Process id' in p.stdout
            and table.count('No running processes found') == 8 and not rows,
            'process_rows': rows[:12], 'health': [x.strip() for x in p.stdout.splitlines() if 'Ascend950' in x][:8]}

def create(kind, run_id):
    gate = idle()
    assert gate['idle'], 'NPU_workload_present'
    assert re.fullmatch(r'[a-z0-9-]{1,50}', run_id)
    name = SPEC['name'] + '-' + kind
    exists = subprocess.run(['docker', 'container', 'inspect', name], capture_output=True, timeout=12)
    assert exists.returncode != 0, 'owned_name_already_exists_inspect_before_retry'
    current = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', IMAGE], timeout=12).decode().strip()
    assert current == IMAGE
    args = ['docker', 'create', '--name', name, '--network', 'host', '--pid', 'host',
        '--privileged', '--shm-size=32g', '--ulimit', 'memlock=-1:-1', '--ulimit', 'stack=67108864:67108864',
        '--mount', f'type=bind,src={ROOT},dst=/experiment',
        '--mount', 'type=bind,src=/mnt/share/GLM-5.2,dst=/model,readonly']
    for p in ['/usr/local/Ascend/driver', '/usr/local/dcmi', '/usr/lib64/urma', '/etc/hixlep', '/etc/hccn.conf']:
        if Path(p).exists():
            args += ['--mount', f'type=bind,src={p},dst={p},readonly']
    for pattern in ['/usr/lib64/libhrn5*', '/usr/lib64/liburma*', '/usr/lib64/libummu*']:
        for p in glob.glob(pattern):
            if Path(p).is_file():
                args += ['--mount', f'type=bind,src={p},dst={p},readonly']
    env = dict(SPEC['environment'])
    env['ASCEND_PROCESS_LOG_PATH'] = f'/experiment/results/{run_id}/ascend-{kind}'
    for key, value in env.items():
        args += ['-e', key + '=' + value]
    args += ['--entrypoint', 'python3', IMAGE]
    if kind == 'model':
        args += ['/experiment/serve.py']
    elif kind == 'validate':
        args += ['/experiment/validate_spec.py']
    else:
        args += ['-m', 'torch.distributed.run', '--nnodes', '2', '--nproc-per-node', '8',
            '--node-rank', str(SPEC['node_rank']), '--master-addr', '173.27.3.58',
            '--master-port', '29652', '/experiment/hccl_smoke.py']
    p = subprocess.run(args, capture_output=True, text=True, timeout=30)
    (ROOT / ('create-' + kind + '.stderr.log')).write_text(p.stderr)
    assert p.returncode == 0, 'container_create_failed'
    cid = p.stdout.strip()
    assert re.fullmatch('[0-9a-f]{64}', cid)
    return {'id': cid, 'name': name, 'kind': kind, 'health': gate['health']}

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['gate', 'create', 'remove', 'state'])
parser.add_argument('--kind', choices=['model', 'validate', 'smoke'])
parser.add_argument('--id')
parser.add_argument('--run-id')
args = parser.parse_args()
if args.action == 'gate':
    print(json.dumps(idle()))
elif args.action == 'create':
    print(json.dumps(create(args.kind, args.run_id)))
else:
    assert re.fullmatch('[0-9a-f]{64}', args.id or '')
    p = subprocess.run(['docker', 'inspect', args.id], capture_output=True, text=True, timeout=12)
    if p.returncode:
        print(json.dumps({'exists': False}))
    else:
        d = json.loads(p.stdout)[0]
        assert d['Name'].startswith('/glm16-09b20820-node') and d['Image'] == IMAGE
        if args.action == 'state':
            print(json.dumps({'exists': True, 'status': d['State']['Status'], 'exit': d['State']['ExitCode']}))
        else:
            p = subprocess.run(['docker', 'rm', '-f', args.id], capture_output=True, timeout=30)
            print(json.dumps({'removed': p.returncode == 0}))
            assert p.returncode == 0
