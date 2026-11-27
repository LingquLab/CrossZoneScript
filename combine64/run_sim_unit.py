# Copyright (c) 2026 TileXR Project.
"""Start and inspect a bounded systemd job owned by this SST session."""
import json
import os
from pathlib import Path
import subprocess
import sys


SESSION = '033634e3-7471-4660-b1c3-784cea442874'
ROOT = Path('/mnt/share/tilexr-sim-jobs') / SESSION


def job_paths(tokens, attempt):
    assert os.uname().nodename.split('.')[0] == 'host-94-26'
    assert ROOT.resolve() == ROOT and ROOT.is_dir()
    assert tokens in (4, 32, 128) and 1 <= attempt <= 20
    unit = f'combine-ht-{SESSION[:8]}-bs{tokens}-a{attempt}'
    return unit, ROOT / 'matrix-jobs' / unit


def run_unit(driver, tokens, attempt, seconds):
    unit, directory = job_paths(tokens, attempt)
    directory.mkdir(parents=True, exist_ok=False)
    script = directory / 'driver.py'
    with script.open('x', encoding='utf-8') as output:
        output.write(driver)
    stdout, stderr = directory / 'driver.stdout', directory / 'driver.stderr'
    exit_file = directory / 'exit-code'
    command_wrapper = (
        'import pathlib,subprocess,sys; '
        'code=subprocess.run(sys.argv[2:]).returncode; '
        'path=pathlib.Path(sys.argv[1]); pending=path.with_suffix(".tmp"); '
        'pending.write_text(str(code)); pending.replace(path); sys.exit(code)'
    )
    command = [
        'systemd-run', '--quiet', '--unit=' + unit, '--property=Type=exec',
        '--property=RuntimeMaxSec=' + str(seconds),
        '--property=TimeoutStopSec=40',
        '--property=StandardOutput=append:' + str(stdout),
        '--property=StandardError=append:' + str(stderr),
        sys.executable, '-c', command_wrapper, str(exit_file), sys.executable, str(script),
    ]
    with (directory / 'systemd-run.log').open('xb') as details:
        proc = subprocess.run(command, stdout=details, stderr=subprocess.STDOUT, timeout=15)
    print(json.dumps({'systemd_unit': unit, 'start_exit': proc.returncode}, separators=(',', ':')))
    return proc.returncode


def inspect_unit(tokens, attempt):
    unit, directory = job_paths(tokens, attempt)
    exit_file = directory / 'exit-code'
    if exit_file.is_file():
        code = int(exit_file.read_text())
        stdout = directory / 'driver.stdout'
        lines = stdout.read_bytes().splitlines() if stdout.is_file() else []
        report = json.loads(lines[-1]) if lines else {}
        return {'state': 'finished', 'code': code, 'report': report, 'systemd_unit': unit}
    state = subprocess.run(['systemctl', 'show', unit + '.service', '--value', '-p', 'ActiveState'],
                           capture_output=True, timeout=8)
    return {'state': state.stdout.decode().strip() or 'unavailable',
            'job_files': directory.exists(), 'systemd_unit': unit}
