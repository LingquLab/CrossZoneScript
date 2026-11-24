# Copyright (c) 2026 TileXR Project.
"""Locate device compiler resources and available simulator inspection tools."""

import json
from pathlib import Path
import subprocess

env = json.loads(Path('/work/environment.snapshot.json').read_text())['settings']
cann = Path(env['cann_root'])
compiler = Path(env['compiler']).resolve()
tools = []
for pattern in ('**/*objdump*', '**/*disasm*', '**/bin/ccec'):
    for path in cann.glob(pattern):
        if path.is_file():
            tools.append(dict(path=str(path), resolved=str(path.resolve())))
proc = subprocess.run([str(compiler), '-print-resource-dir'], capture_output=True, text=True, timeout=15)
resource = proc.stdout.strip() if proc.returncode == 0 else ''
roots = [compiler.parent.parent / 'include']
if resource and '\n' not in resource:
    roots.append(Path(resource) / 'include')
hits = []
for root in roots:
    if not root.is_dir():
        continue
    for path in root.glob('**/*.h'):
        if not path.is_file() or path.stat().st_size > 2000000:
            continue
        lines = path.read_text(errors='replace').splitlines()
        for index, line in enumerate(lines):
            if 'st_dev(' in line or 'st_dev (' in line:
                hits.append(dict(path=str(path), excerpt=lines[max(0,index-1):index+5]))
                break
        if len(hits) >= 3:
            break
print(json.dumps(dict(ok=True, compiler=str(compiler), tools=tools[:20], resource=resource[:300], st_dev_headers=hits[:3]), ensure_ascii=True))
