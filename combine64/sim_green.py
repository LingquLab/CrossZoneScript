# Copyright (c) 2026 TileXR Project.
"""Launch one bounded simulator action through the green SSH account."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import traceback

SESSION = 'de038086-2b21-40e2-bad9-65360186ad97'
SCRIPT_FILES = ('fixture.py', 'prefill.cpp', 'sim_case.py', 'return_case.py', 'addressed_exports.py')
STAGES = ('local-send', 'gateway-reduce', 'local-return',
           'inter-server-send', 'final-independent', 'joint-producer',
           'final-from-producer', 'joint-final')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    action, tokens = args['action'], args.get('tokens', 4)
    attempt = args.get('attempt', 1)
    assert action in ('status', 'batch', *STAGES) and tokens in (4, 16, 32, 64, 128)
    assert type(attempt) is int and 1 <= attempt <= 20
    candidate = args.get('candidate', '86f802f')
    assert candidate in ('b5383cf', '86f802f')
    timeout = args.get('timeout', 900)
    assert type(timeout) is int and 30 <= timeout <= 7200
    remote_seconds = timeout + 300
    if action == 'batch':
        start = STAGES.index(args.get('start_stage', STAGES[0]))
        end = STAGES.index(args.get('end_stage', STAGES[-1]))
        assert start <= end
        stage_timeouts = args.get('stage_timeouts', {})
        assert stage_timeouts.keys() <= set(STAGES)
        assert all(type(value) is int and 30 <= value <= 7200 for value in stage_timeouts.values())
        remote_seconds = 90 + sum(stage_timeouts.get(stage, timeout) + 260 for stage in STAGES[start:end + 1])
    if args.get('require_bs4_gateway'):
        remote_seconds += 150
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
        'root@141.61.94.26', f'timeout --kill-after=30 {remote_seconds} python3 -',
    ]
    stem = logs / f'{action}-t{tokens}-a{attempt}'
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
