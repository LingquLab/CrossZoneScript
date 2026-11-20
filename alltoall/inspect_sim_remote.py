"""Find only simulator resources and CPU container prerequisites."""
import json
from pathlib import Path
import shutil
import subprocess


def query(argv):
    try:
        p = subprocess.run(argv, capture_output=True, timeout=20)
        return {'exit_code': p.returncode, 'output': p.stdout.decode('utf-8', 'replace')[:16000]}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {'error_type': type(e).__name__}


def entries(path, limit=40):
    p = Path(path)
    return {'path': path, 'exists': p.is_dir(), 'entries':
            [{'name': x.name, 'directory': x.is_dir()} for x in sorted(p.iterdir())[:limit]] if p.is_dir() else []}


image = 'tilexr-sim-9.1:aarch64-ac2cee81-v1.0.0'
result = {'resources': [entries(p) for p in ('/mnt/share', '/mnt/share/simulator', '/opt/sim')]}
result['docker_runtimes'] = query(['docker', 'info', '--format', '{{json .Runtimes}}'])
result['image_config'] = query(['docker', 'image', 'inspect', image, '--format',
    '{{json .Id}} {{json .Architecture}} {{json .Config.Entrypoint}} {{json .Config.Cmd}} {{json .Config.Volumes}}'])
result['root_disk_free_bytes'] = shutil.disk_usage('/').free
result['share_disk_free_bytes'] = shutil.disk_usage('/mnt/share').free if Path('/mnt/share').exists() else None
for item in result['resources'][:2]:
    root = Path(item['path'])
    item['simulator_children'] = [entries(str(root / row['name']), 25) for row in item['entries']
        if row['directory'] and any(word in row['name'].lower() for word in ('sim', 'tilexr', 'alltoall'))][:10]
print(json.dumps(result, ensure_ascii=True))
