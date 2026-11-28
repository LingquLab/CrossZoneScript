"""Resume incomplete attempts after proving the previous host workers have exited."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import time

from bootstrap_green import SESSION
from gateway_download import download, DESTINATION, report
from gateway_green import HERE, execute
from transport import connect

MATRIX = [(cores, chunk) for cores in (8, 16, 24, 32) for chunk in (16, 32, 64, 128)]
MICRO = [(1, chunk) for chunk in (16, 32, 64, 128, 512)]
FILES = ('gateway_fixture.py', 'gateway_args.cpp', 'gateway_case.py', 'gateway_profile.py',
         'addressed_exports.py', 'clock_evidence.json')


def host_call(args, seconds, logs):
    logs.mkdir(parents=True, exist_ok=False)
    with connect('18', 'password') as client:
        return execute(client, 'gateway_host.py', args, seconds, logs)


def case_points(state):
    points = {}
    for row in state['rows']:
        match = re.fullmatch(r'cores([0-9]+)-chunk([0-9]+)-a([0-9]+)', row['case'])
        assert match is not None
        cores, chunk, attempt = map(int, match.groups())
        points.setdefault((cores, chunk), {})[attempt] = row
    return points


def latest_pass(attempts):
    return next((attempts[key] for key in sorted(attempts)
                 if attempts[key]['status'] == 'sst_and_checker_passed'), None)


def snapshot(logs):
    for _ in range(3):
        try:
            code, state = host_call({'action': 'status', 'show_legacy_coordinator': True}, 60,
                                    logs / f'status-{time.time_ns()}')
            assert code == 0 and 'active' in state and 'rows' in state
            return state
        except (AssertionError, OSError, EOFError):
            time.sleep(5)
    raise RuntimeError('status_unavailable')


def wait_for_prior_runs(logs, seconds):
    deadline = time.monotonic() + seconds
    while True:
        state = snapshot(logs)
        live = [name for name in state['active'] if re.fullmatch(
            rf'combine-ht-{SESSION[:8]}-core[0-9]+-[0-9]+-a[0-9]+', name)]
        if not live and not state['legacy_coordinator_running']:
            return state
        if time.monotonic() >= deadline:
            raise RuntimeError('previous_workers_still_running')
        time.sleep(30)


def run_one(point, attempt, files, logs):
    try:
        cores, chunk = point
        timeout = 2700 if point == (1, 512) else 1800
        code, payload = host_call({'action': 'run', 'points': [list(point)], 'workers': 1, 'files': files,
                                   'timeout': timeout, 'attempt': attempt}, timeout + 600,
                                  logs / f'run-{cores}-{chunk}-a{attempt}')
        return {'point': point, 'attempt': attempt, 'exit': code,
                'status': payload.get('rows', [{}])[0].get('status')}
    except Exception as error:
        return {'point': point, 'attempt': attempt, 'error_class': type(error).__name__}


def complete(points, workers, logs):
    state = wait_for_prior_runs(logs, 1500)
    attempts = case_points(state)
    missing = [(point, max(attempts.get(point, {0: None})) + 1) for point in points
               if latest_pass(attempts.get(point, {})) is None]
    if missing:
        files = {name: (HERE / name).read_text(encoding='utf-8') for name in FILES}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            jobs = [executor.submit(run_one, point, attempt, files, logs) for point, attempt in missing]
            dispatch_results = [job.result() for job in jobs]
        (logs / f'resumed-{points[0][0]}.json').write_text(json.dumps(dispatch_results), encoding='utf-8')
        state = wait_for_prior_runs(logs, 900)
        attempts = case_points(state)
    passed = [latest_pass(attempts.get(point, {})) for point in points]
    assert all(passed), {'failing_points': [point for point, row in zip(points, passed) if row is None]}
    return passed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    workers = args.get('workers', 4)
    assert type(workers) is int and 1 <= workers <= 4
    logs = Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr' / SESSION / f'resume-{time.time_ns()}'
    logs.mkdir(parents=True)
    try:
        matrix = complete(MATRIX, workers, logs)
        micro = complete(MICRO, workers, logs)
        rows, traces = [], {}
        DESTINATION.mkdir(parents=True, exist_ok=True)
        with connect('18', 'password') as client, client.open_sftp() as sftp:
            sftp.get_channel().settimeout(120)
            for entry in matrix + micro:
                row, traces[entry['case']] = download(sftp, entry['case'])
                rows.append(row)
        report(rows, traces)
        print(json.dumps({'verified_cases': matrix + micro, 'trace_count': len(traces),
                          'directory': str(DESTINATION), 'report_written': True}, separators=(',', ':')))
        return 0
    except Exception as error:
        (logs / 'failure.txt').write_text(f'{type(error).__name__}: {error}', encoding='utf-8')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
