# Copyright (c) 2026 TileXR Project.
"""Launch one bounded simulator action through the green SSH account."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import traceback

SESSION = '44b9c060-a5dd-46a8-897f-e4a6fee171e2'
SCRIPT_FILES = ('fixture.py', 'prefill.cpp', 'sim_case.py', 'return_case.py', 'addressed_exports.py', 'build_profile.py',
                'profile_capture.h', 'profile_case.py', 'clock_evidence.json', 'inter_snapshot.py')
STAGES = ('local-send', 'gateway-reduce', 'local-return',
           'inter-server-send', 'final-independent', 'joint-final')



CASE_OVERHEAD = {4: 260, 16: 260, 32: 430, 64: 630, 128: 930}


def stage_budget(stages, timeouts, default, tokens):
    assert timeouts.keys() <= set(STAGES)
    assert all(type(value) is int and 30 <= value <= 7200 for value in timeouts.values())
    return sum(timeouts.get(stage, default) + CASE_OVERHEAD[tokens] for stage in stages)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    action, tokens = args['action'], args.get('tokens', 4)
    attempt = args.get('attempt', 1)
    assert action in ('status', 'profile-build', 'batch', *STAGES)
    assert tokens in (4, 16, 32, 64, 128)
    assert type(attempt) is int and 1 <= attempt <= 20
    candidate = args['candidate']
    assert len(candidate) == 7 and all(c in '0123456789abcdef' for c in candidate)
    assert type(args.get('profiling', False)) is bool
    timeout = args.get('timeout', 900)
    assert type(timeout) is int and 30 <= timeout <= 7200
    if 'stop_at_us' in args:
        expected_attempt = 4 if args.get('ready_probe') else 2
        assert (action, tokens, attempt, args.get('profiling')) == ('local-return', 4, expected_attempt, True)
        assert type(args['stop_at_us']) is int and 20 <= args['stop_at_us'] <= 200
    remote_seconds = timeout + CASE_OVERHEAD[tokens] + 40
    if action == 'status':
        remote_seconds = 35
    if action == 'profile-build':
        remote_seconds = timeout + 45
    if action == 'batch':
        start = STAGES.index(args.get('start_stage', STAGES[0]))
        end = STAGES.index(args.get('end_stage', STAGES[-1]))
        assert start <= end
        remote_seconds = 90 + stage_budget(STAGES[start:end + 1], args.get('stage_timeouts', {}), timeout, tokens)
    if 'after' in args:
        assert action == 'batch'
        wait_seconds = args['after']['max_wait_seconds']
        assert type(wait_seconds) is int and 60 <= wait_seconds <= 7200
        remote_seconds += wait_seconds + 30
    if args.get('ready_probe'):
        assert 'stop_at_us' in args and 30 <= args['wait_for_idle_seconds'] <= 800
        remote_seconds += 215 + 2 * args['wait_for_idle_seconds']
    elif 'memory_gib' in args or 'wait_for_idle_seconds' in args:
        wait = args['wait_for_idle_seconds']
        assert type(wait) is int and 30 <= wait <= 600
        assert args['memory_gib'] == 64 and action == 'batch'
        remote_seconds += wait + 45
    logs = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr' / SESSION / candidate
    logs.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).parent
    args.update(tokens=tokens, attempt=attempt, timeout=timeout)
    args['files'] = {name: (here / name).read_text(encoding='utf-8') for name in SCRIPT_FILES}
    code = (here / 'sim_host.py').read_text(encoding='utf-8')
    code += '\nraise SystemExit(main(' + repr(args) + '))\n'
    ssh = [
        'ssh', '-T', '-i', str(Path(os.environ['USERPROFILE']) / '.ssh/id_ed25519'),
        '-p', '22', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
        '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=12',
        '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3',
        'root@141.61.94.26', f'timeout --kill-after=30 {remote_seconds} python3 -',
    ]
    variant = args.get('start_stage', action) if action == 'batch' else action
    stem = logs / f'{variant}-t{tokens}-a{attempt}'
    try:
        proc = subprocess.run(ssh, input=code.encode(), capture_output=True, timeout=remote_seconds + 50)
        stem.with_suffix('.stdout').write_bytes(proc.stdout)
        stem.with_suffix('.stderr').write_bytes(proc.stderr)
        report = json.loads(proc.stdout) if proc.stdout.strip() else {'stage': 'ssh'}
        report.update(ssh_exit=proc.returncode, ssh_stderr_bytes=len(proc.stderr))
        print(json.dumps(report, separators=(',', ':')))
        return proc.returncode
    except Exception as error:
        stem.with_suffix('.error').write_text(traceback.format_exc(), encoding='utf-8')
        print(json.dumps({'stage': action, 'error_class': type(error).__name__, 'evidence': str(stem)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
