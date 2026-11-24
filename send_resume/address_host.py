# Copyright (c) 2026 TileXR Project.
"""Read completed simulator address evidence inside the verified container."""

import json
import socket
import subprocess

NAME = 'tilexr-combine-ht-500e2a93-v11'
SOURCE = '__CONTAINER_SOURCE__'
assert socket.gethostname().split('.')[0] == 'host-94-26'
proc = subprocess.run(['docker', 'inspect', '--format', '{{.Id}}', NAME], capture_output=True, text=True, timeout=25)
assert proc.returncode == 0 and proc.stdout.strip() == 'e9ab0e97f6b31670b6ce68412fdf20c15203502b9aa8fc0840d45e927a0254a4'
proc = subprocess.run(['docker', 'top', NAME, '-eo', 'pid,comm,stat'], capture_output=True, text=True, timeout=25)
assert proc.returncode == 0
rows = [line.split() for line in proc.stdout.splitlines()[1:]]
assert not any(row[1] in ('sst', 'sstsim.x') and not row[2].startswith('Z') for row in rows), 'sst_active'
proc = subprocess.run(['docker', 'exec', '-i', NAME, 'python3', '-'], input=SOURCE.encode(), capture_output=True, timeout=100)
assert proc.returncode == 0, 'container_inspection_failed'
result = json.loads(proc.stdout)
assert result['ok'] and len(proc.stdout) < 18000
print(json.dumps(result, ensure_ascii=True))
