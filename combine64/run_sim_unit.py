# Copyright (c) 2026 TileXR Project.
"""Launch a session-owned SST matrix in a bounded foreground systemd unit."""
import json
import os
from pathlib import Path
import subprocess
import sys


SESSION = '033634e3-7471-4660-b1c3-784cea442874'
ROOT = Path('/mnt/share/tilexr-sim-jobs') / SESSION


def run_unit(driver, tokens, attempt, seconds):
    assert os.uname().nodename.split('.')[0] == 'host-94-26'
    assert ROOT.resolve() == ROOT and ROOT.is_dir()
    assert tokens in (4, 32, 128) and 1 <= attempt <= 20
    unit = f'combine-ht-{SESSION[:8]}-bs{tokens}-a{attempt}'
    directory = ROOT / 'matrix-jobs' / unit
    directory.mkdir(parents=True, exist_ok=False)
    script = directory / 'driver.py'
    with script.open('x', encoding='utf-8') as output:
        output.write(driver)
    stdout, stderr = directory / 'driver.stdout', directory / 'driver.stderr'
    command = [
        'systemd-run', '--wait', '--quiet', '--unit=' + unit,
        '--property=RuntimeMaxSec=' + str(seconds),
        '--property=TimeoutStopSec=40',
        '--property=StandardOutput=append:' + str(stdout),
        '--property=StandardError=append:' + str(stderr),
        sys.executable, str(script),
    ]
    with (directory / 'systemd-run.log').open('xb') as details:
        proc = subprocess.run(command, stdout=details, stderr=subprocess.STDOUT, timeout=seconds + 60)
    lines = stdout.read_bytes().splitlines() if stdout.is_file() else []
    report = json.loads(lines[-1]) if lines else {'stage': 'systemd-unit'}
    report['systemd_unit'] = unit
    report['systemd_exit'] = proc.returncode
    print(json.dumps(report, separators=(',', ':')), flush=True)
    return proc.returncode
