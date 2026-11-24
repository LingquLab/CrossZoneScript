# Copyright (c) 2026 TileXR Project.
"""Read actual fatal context, trial state, and tool locations from retained files."""

import json
from pathlib import Path
import re
import socket
import subprocess

WORK = Path('/var/tmp/tilexr-combine-ht-500e2a93-b84e-4d36-a9aa-8d2cd0321cdf-v11')
NAME = 'tilexr-combine-ht-500e2a93-v11'
TOOLS_SOURCE = '__TOOLS_SOURCE__'
assert socket.gethostname().split('.')[0] == 'host-94-26'
proc = subprocess.run(['docker', 'inspect', '--format', '{{.Id}}', NAME], capture_output=True, text=True, timeout=25)
assert proc.returncode == 0 and proc.stdout.strip() == 'e9ab0e97f6b31670b6ce68412fdf20c15203502b9aa8fc0840d45e927a0254a4'
proc = subprocess.run(['docker', 'top', NAME, '-eo', 'pid,comm,stat'], capture_output=True, text=True, timeout=25)
assert proc.returncode == 0
rows = [line.split() for line in proc.stdout.splitlines()[1:]]
result = dict(ok=True, read_only=True, no_sst_active=not any(row[1] in ('sst', 'sstsim.x') and not row[2].startswith('Z') for row in rows), trials=[])
for name in ('manual-a1', 'address-transform-a1'):
    folder = WORK / 'resume-7de1' / name
    row = dict(trial=name, exists=folder.is_dir())
    path = folder / 'result.json'
    if path.is_file():
        saved = json.loads(path.read_text())
        row['result'] = {k: v for k, v in saved.items() if k not in ('transfers', 'send')}
        if 'send' in saved:
            row['send'] = {k: v for k, v in saved['send'].items() if k not in ('model', 'verified_workspace_sha256', 'profiling')}
    log = folder / 'send-only/sim.log'
    if log.is_file():
        first, following, after = [], [], 0
        with log.open(errors='replace') as handle:
            for line in handle:
                match = re.search(r'FATAL|Unknown low level|[Aa]ssertion|Segmentation fault', line)
                if match and len(first) < 2:
                    first.append(line[max(0,match.start()-50):match.start()+1200].strip())
                    after = 3
                elif after and len(following) < 6:
                    following.append(line.strip()[:500])
                    after -= 1
        row.update(fatal_context=first, following_lines=following)
    result['trials'].append(row)
proc = subprocess.run(['docker', 'exec', '-i', NAME, 'python3', '-'], input=TOOLS_SOURCE.encode(), capture_output=True, timeout=65)
result['tool_inspection_exit'] = proc.returncode
if proc.returncode == 0:
    result['tools'] = json.loads(proc.stdout)
print(json.dumps(result, ensure_ascii=True))
