# Copyright (c) 2026 TileXR Project.
"""Invoke the pinned Send diagnostic through the approved green SSH connection."""

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

SESSION = '7de11bac-47e4-498d-b107-e746122e3b0a'


def main(args):
    assert args in ({'action': 'mte-a1'}, {'action': 'inspect-mte-a1'}, {'action': 'inspect-address-a1'})
    directory = Path(__file__).parent
    if args['action'] == 'mte-a1':
        host = (directory / 'mte_host.py').read_text(encoding='utf-8')
        trial = (directory / 'mte_trial.py').read_text(encoding='utf-8')
        assert host.count("'__TRIAL_SOURCE__'") == 1
        host = host.replace("'__TRIAL_SOURCE__'", repr(trial))
    elif args['action'] == 'inspect-mte-a1':
        host = (directory / 'inspect_host.py').read_text(encoding='utf-8')
    else:
        host = (directory / 'address_host.py').read_text(encoding='utf-8')
        host = host.replace("'__CONTAINER_SOURCE__'", repr((directory / 'address_container.py').read_text(encoding='utf-8')))
    ast.parse(host)
    logs = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr' / SESSION
    logs.mkdir(parents=True, exist_ok=True)
    key = Path(os.environ['USERPROFILE']) / '.ssh/id_ed25519'
    command = ['ssh', '-T', '-i', str(key), '-p', '22', '-o', 'BatchMode=yes',
               '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'ConnectTimeout=12', 'root@141.61.94.26',
               'timeout --kill-after=10 620 python3 -']
    proc = subprocess.run(command, input=host.encode(), capture_output=True, timeout=650)
    (logs / (args['action'] + '.stdout')).write_bytes(proc.stdout)
    (logs / (args['action'] + '.stderr')).write_bytes(proc.stderr)
    result = json.loads(proc.stdout)
    assert len(proc.stdout) < 25000, 'export_too_large'
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['ok'] and proc.returncode == 0 else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    try:
        args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
        raise SystemExit(main(args))
    except Exception as error:
        print(json.dumps(dict(ok=False, error_type=type(error).__name__, detail='see_green_local_mte_a1_log')))
        raise SystemExit(1)
