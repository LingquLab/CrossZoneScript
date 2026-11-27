# Copyright (c) 2026 TileXR Project.
"""Start and inspect a bounded systemd job owned by this SST session."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


SESSION = '033634e3-7471-4660-b1c3-784cea442874'
ROOT = Path('/mnt/share/tilexr-sim-jobs') / SESSION


def job_paths(tokens, attempt):
    assert os.uname().nodename.split('.')[0] == 'host-94-26'
    assert ROOT.resolve() == ROOT and ROOT.is_dir()
    assert tokens in (4, 32, 128) and 1 <= attempt <= 20
    unit = f'combine-ht-{SESSION[:8]}-bs{tokens}-a{attempt}'
    return unit, ROOT / 'matrix-jobs' / unit


def process_tree(pid):
    found, pending = set(), [pid]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        children = Path('/proc') / str(current) / 'task' / str(current) / 'children'
        try:
            pending.extend(map(int, children.read_text().split()))
        except FileNotFoundError:
            pass
    return found


def trace_senders(unit, directory):
    main_pid = int(subprocess.check_output(
        ['systemctl', 'show', unit + '.service', '--value', '-p', 'MainPID'], timeout=8))
    assert main_pid > 1
    deadline = time.monotonic() + 3
    targets = process_tree(main_pid)
    while len(targets) < 3 and time.monotonic() < deadline:
        time.sleep(0.1)
        targets = process_tree(main_pid)
    assert len(targets) >= 3
    instance = Path('/sys/kernel/tracing/instances') / unit
    instance.mkdir()
    try:
        (instance / 'tracing_on').write_text('0')
        event = instance / 'events/signal/signal_generate'
        targets = ' || '.join(f'pid == {pid}' for pid in sorted(targets))
        (event / 'filter').write_text(f'(sig == 15 || sig == 9) && ({targets})')
        (event / 'enable').write_text('1')
        (instance / 'tracing_on').write_text('1')
        (directory / 'signal-instance').write_text(str(instance))
    except BaseException:
        (instance / 'tracing_on').write_text('0')
        instance.rmdir()
        raise


def run_unit(driver, tokens, attempt, seconds, trace_signals=False):
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
    workload = [sys.executable, '-c', command_wrapper, str(exit_file), sys.executable, str(script)]
    if trace_signals:
        workload = ['strace', '-f', '-tt', '-e', 'trace=none',
                    '-e', 'signal=TERM,HUP,INT,QUIT,KILL',
                    '-o', str(directory / 'host-signals.log'), *workload]
    command = [
        'systemd-run', '--quiet', '--unit=' + unit, '--property=Type=exec',
        '--property=RuntimeMaxSec=' + str(seconds),
        '--property=TimeoutStopSec=40',
        '--property=StandardOutput=append:' + str(stdout),
        '--property=StandardError=append:' + str(stderr),
        *workload,
    ]
    with (directory / 'systemd-run.log').open('xb') as details:
        proc = subprocess.run(command, stdout=details, stderr=subprocess.STDOUT, timeout=15)
    if trace_signals and proc.returncode == 0:
        trace_senders(unit, directory)
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
