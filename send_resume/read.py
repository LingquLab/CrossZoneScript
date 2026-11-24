# Copyright (c) 2026 TileXR Project.
"""Retrieve bounded saved Send results without repeating any build or simulation."""

import argparse
import json
import os
from pathlib import Path
import subprocess

SESSION = '7de11bac-47e4-498d-b107-e746122e3b0a'
parser = argparse.ArgumentParser()
parser.add_argument('--args-file', type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
assert args == {'trial': 'address-transform-a1'}
directory = Path(__file__).parent
host = (directory / 'read_host.py').read_text(encoding='utf-8')
host = host.replace("'__TOOLS_SOURCE__'", repr((directory / 'read_tools.py').read_text(encoding='utf-8')))
logs = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr' / SESSION
saved = logs / 'address-transform-a1.stdout'
result = dict(ok=True, read_only=True)
if saved.is_file() and saved.stat().st_size:
    result['launcher_result'] = json.loads(saved.read_bytes())
key = Path(os.environ['USERPROFILE']) / '.ssh/id_ed25519'
command = ['ssh', '-T', '-i', str(key), '-p', '22', '-o', 'BatchMode=yes',
    '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=12',
    'root@141.61.94.26', 'timeout --kill-after=5 100 python3 -']
proc = subprocess.run(command, input=host.encode(), capture_output=True, timeout=125)
(logs / 'read-address-transform-a1.stdout').write_bytes(proc.stdout)
(logs / 'read-address-transform-a1.stderr').write_bytes(proc.stderr)
assert proc.returncode == 0 and len(proc.stdout) < 25000, 'saved_result_read_failed'
result['remote'] = json.loads(proc.stdout)
assert len(json.dumps(result).encode()) < 45000
print(json.dumps(result, ensure_ascii=True))
