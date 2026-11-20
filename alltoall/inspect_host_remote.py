"""Read-only host inventory; never access NPU devices."""
import json
import os
from pathlib import Path
import platform
import socket
import subprocess


def query(argv):
    try:
        p = subprocess.run(argv, capture_output=True, timeout=15)
        return {'exit_code': p.returncode, 'lines': p.stdout.decode('utf-8', 'replace').splitlines()[:30]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'error_type': type(exc).__name__}


root = Path('/mnt/share/simlulator')
result = dict(hostname=socket.gethostname(), architecture=platform.machine(), cpu_count=os.cpu_count())
result['docker_images'] = query(['docker', 'image', 'ls', '--no-trunc', '--format', '{{.Repository}}:{{.Tag}} {{.ID}}'])
result['docker_containers'] = query(['docker', 'ps', '--format', '{{.Names}} {{.Image}}'])
result['image_directory'] = {'path': str(root), 'exists': root.is_dir(),
                             'entries': sorted(p.name for p in root.iterdir())[:30] if root.is_dir() else []}
print(json.dumps(result, ensure_ascii=True))
