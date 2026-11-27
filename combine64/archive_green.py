# Copyright (c) 2026 TileXR Project.
"""Run the pinned old-case archive operation through the established SSH account."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    assert set(args) == {'names'}
    source = Path(__file__).with_name('archive_old_cases.py').read_text(encoding='utf-8')
    code = source + '\narchive(' + repr(args['names']) + ')\n'
    key = Path(os.environ['USERPROFILE']) / '.ssh/id_ed25519'
    command = ['ssh', '-T', '-i', str(key), '-p', '22', '-o', 'BatchMode=yes',
               '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
               'root@141.61.94.26', 'timeout --kill-after=4 160 python3 -']
    run = subprocess.run(command, input=code.encode(), capture_output=True, timeout=175)
    if run.returncode == 0:
        print(json.dumps(json.loads(run.stdout), separators=(',', ':')))
    return run.returncode


if __name__ == '__main__':
    raise SystemExit(main())
