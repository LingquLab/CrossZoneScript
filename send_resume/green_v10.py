# Copyright (c) 2026 TileXR Project.
"""Run the pinned V1.0 perf comparison through the green bridge's SSH connection."""

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
assert args == {'action': 'v10-a1'}
directory = Path(__file__).parent
host = (directory / 'v10_host.py').read_text(encoding='utf-8').replace("'__CONTAINER_SOURCE__'",repr((directory / 'v10_container.py').read_text(encoding='utf-8')))
ast.parse(host)
logs = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr/7de11bac-47e4-498d-b107-e746122e3b0a'
logs.mkdir(parents=True,exist_ok=True)
key = Path(os.environ['USERPROFILE']) / '.ssh/id_ed25519'
command = ['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
    '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26','timeout --kill-after=15 950 python3 -']
proc = subprocess.run(command,input=host.encode(),capture_output=True,timeout=980)
(logs / 'v10-a1.stdout').write_bytes(proc.stdout)
(logs / 'v10-a1.stderr').write_bytes(proc.stderr)
result = json.loads(proc.stdout)
assert len(proc.stdout)<40000
print(json.dumps(result,ensure_ascii=True))
raise SystemExit(0 if result['ok'] and proc.returncode==0 else 1)
