# Copyright (c) 2026 TileXR Project.
"""Check the task's container and launch one foreground SST case."""
import fcntl
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
import traceback

SESSION = 'de038086-2b21-40e2-bad9-65360186ad97'
REVISIONS = {
    'b5383cf': 'b5383cfd65828a165fae9957d287967136c06260',
    '86f802f': '86f802f9f8a19728364c466b0e9cb7416e038595',
    'cb63658': 'cb63658b507aa7eba023afefd9e7ef8a27a0dcde',
}
SESSION_ROOT = Path(f'/mnt/share/tilexr-sim-jobs/{SESSION}')
TEMPLATE = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549')
IMAGE = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
PLATFORM_FILES = {'test_UBusSim.py', 'sst-environment.json', 'environment.snapshot.json', 'installed_model.py'}
ERROR_PATTERN = re.compile(r'(fatal error:|RuntimeError:|ValueError:|AssertionError|FileNotFoundError|PermissionError|error:)')


def json_file(path):
    return json.loads(path.read_text())


def digest(path):
    with path.open('rb') as stream:
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
        return value.hexdigest()


def task_container(root, revision):
    identity = json_file(root / 'identity.json')
    assert identity['revision'] == revision and identity['image'] == IMAGE
    cid = identity['container_id']
    info = json.loads(subprocess.check_output(['docker', 'inspect', cid], timeout=20))[0]
    assert info['Id'] == cid and info['Image'] == IMAGE
    assert info['HostConfig']['NetworkMode'] == 'none'
    assert info['Config']['Labels']['crosszone.session'] == SESSION
    assert info['State']['Running']
    mounts = {m['Destination']: m for m in info['Mounts']}
    assert mounts['/work']['Source'] == str(root)
    assert mounts['/work/source']['Source'] == str(root / 'source')
    assert mounts['/work/source']['RW'] is False
    return cid


def stage_tools(root, files):
    key = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]
    tools = root / 'tools' / key
    tools.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        assert name in {'fixture.py', 'prefill.cpp', 'sim_case.py', 'return_case.py', 'addressed_exports.py',
                        'build_profile.py', 'profile_capture.h', 'profile_case.py', 'clock_evidence.json',
                        'inter_snapshot.py'}
        path = tools / name
        if path.exists():
            assert path.read_bytes() == content.encode()
        else:
            path.write_bytes(content.encode())
    platform = root / 'platform'
    platform.mkdir(exist_ok=True)
    assert json_file(TEMPLATE / 'run.json')['status'] == 'sst_and_checker_passed'
    pinned = json_file(TEMPLATE / 'case-manifest.json')['files']
    selected = {}
    for name, expected in pinned.items():
        relative = PurePosixPath(name)
        assert not relative.is_absolute() and '..' not in relative.parts
        if name not in PLATFORM_FILES and relative.parts[0] not in {'v310_config', 'UBusSim_config'}:
            continue
        if name == 'v310_config/workload_config.yml':
            continue
        assert digest(TEMPLATE / name) == expected, name
        path = platform / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            shutil.copyfile(TEMPLATE / name, path)
        assert digest(path) == expected, name
        selected[name] = expected
    assert PLATFORM_FILES <= selected.keys(), 'platform_files_missing'
    assert all(any(name.startswith(prefix + '/') for name in selected)
               for prefix in ('v310_config', 'UBusSim_config'))
    return tools, selected



STAGES = (
    'local-send', 'gateway-reduce', 'local-return', 'inter-server-send',
    'final-independent', 'joint-producer', 'final-from-producer', 'joint-final',
)
REPORT_FIELDS = (
    'candidate_commit', 'stage', 'tokens', 'attempt', 'status', 'blocks',
    'routes_sha256', 'source_object_sha256', 'kernel_sha256',
    'environment_sha256', 'platform_simulated_us', 'sst_wall_seconds',
    'runner_wall_seconds', 'guards', 'checked_ranks', 'checked_bytes',
    'predecessor', 'actual_input_sha256', 'model_config', 'error',
    'profiling_enabled', 'profiling_operator_us', 'profiling_max_lane_ticks',
    'profiling_lanes', 'profiling_dropped_records', 'profiling_clock_receipt',
    'diagnostic_only', 'stop_at_us', 'diagnostic_source', 'lane_status_histogram', 'incomplete_lanes',
    'incomplete_lane_count', 'pending_queues', 'pending_queue_count', 'missing_exports',
)


def run_case(root, cid, tools, config):
    action, tokens, attempt, timeout = (config[key] for key in ('action', 'tokens', 'attempt', 'timeout'))
    case = root / 'cases' / f't{tokens}-{action}-a{attempt}'
    logs = root / 'hapi-logs'
    logs.mkdir(exist_ok=True)
    log_path = logs / (case.name + '.log')
    container_tools = str(Path('/work') / tools.relative_to(root))
    overhead = {4: 230, 16: 230, 32: 400, 64: 600, 128: 900}[tokens]
    argv = [
        'docker', 'exec', cid, 'timeout', '--kill-after=20', str(timeout + overhead),
        'python3', f'{container_tools}/sim_case.py', json.dumps(config),
    ]
    with log_path.open('xb') as log:
        proc = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, timeout=timeout + overhead + 30)
    result_path = case / 'phase-result.json'
    if result_path.is_file():
        report = json_file(result_path)
    else:
        text = log_path.read_text(errors='replace')
        errors = [line.strip()[:250] for line in text.splitlines() if ERROR_PATTERN.search(line)]
        report = {'stage': action, 'status': 'preparation_or_runner_failed', 'error': errors[:5]}
    summary = {key: report[key] for key in REPORT_FIELDS if key in report}
    summary.update(case=str(case), host_log=str(log_path), container_exit=proc.returncode)
    return summary


def status(root, args):
    report = {'root': str(root), 'exists': root.is_dir()}
    for name in ('progress.json', 'build-summary.json'):
        path = root / name
        if path.is_file():
            report[path.stem] = json_file(path)
    tokens, attempt = args['tokens'], args['attempt']
    batch = root / 'hapi-logs' / f't{tokens}-batch-a{attempt}.json'
    if batch.is_file():
        report['batch'] = json_file(batch)
    stage = args.get('status_for')
    if stage in STAGES:
        case = root / 'cases' / f't{tokens}-{stage}-a{attempt}'
        for name in ('phase-result.json', 'run.json'):
            path = case / name
            if path.is_file():
                run = json_file(path)
                report[path.stem] = {key: run[key] for key in REPORT_FIELDS if key in run}
    return report


def run_batch(root, cid, tools, config, args):
    tokens, attempt = config['tokens'], config['attempt']
    logs = root / 'hapi-logs'
    logs.mkdir(exist_ok=True)
    journal = logs / f't{tokens}-batch-a{attempt}.json'
    start = STAGES.index(args.get('start_stage', STAGES[0]))
    end = STAGES.index(args.get('end_stage', STAGES[-1]))
    assert start <= end, 'invalid_stage_interval'
    reports = []
    with journal.open('x') as log:
        json.dump(reports, log)
    stage_timeouts = args.get('stage_timeouts', {})
    for action in STAGES[start:end + 1]:
        point = dict(config, action=action, timeout=stage_timeouts.get(action, config['timeout']))
        if action == 'final-from-producer':
            point['source_case'] = args.get('source_case') or f't{tokens}-joint-producer-a{attempt}'
        reports.append(run_case(root, cid, tools, point))
        pending = journal.with_suffix('.tmp')
        pending.write_text(json.dumps(reports, indent=2) + '\n')
        pending.replace(journal)
        if reports[-1]['status'] != 'sst_and_checker_passed' or reports[-1]['container_exit'] != 0:
            break
    return reports



def require_bs4_gateway(deadline):
    baseline = SESSION_ROOT / 'run-b5383cf-a1' / 'cases/t4-gateway-reduce-a1/phase-result.json'
    while not baseline.is_file() and time.monotonic() < deadline:
        time.sleep(2)
    assert baseline.is_file(), 'baseline_gateway_result_pending'
    result = json_file(baseline)
    assert result.get('status') == 'sst_and_checker_passed' and result.get('checked_ranks') == 64, 'baseline_gateway_not_passed'
    assert (result['candidate_commit'], result['tokens'], result['stage']) == (REVISIONS['b5383cf'], 4, 'gateway-reduce')


def require_previous_bs(root, revision, tokens, attempt=1):
    sizes = (4, 16, 32, 64, 128)
    assert tokens != sizes[0], 'no_preceding_batch_size'
    previous = sizes[sizes.index(tokens) - 1]
    journal = root / 'hapi-logs' / f't{previous}-batch-a{attempt}.json'
    assert journal.is_file(), 'preceding_batch_result_missing'
    rows = json_file(journal)
    expected = STAGES[2:] if previous == 4 else STAGES
    assert tuple(row.get('stage') for row in rows) == expected, 'preceding_batch_incomplete'
    assert all(row.get('candidate_commit') == revision and row.get('tokens') == previous
               and row.get('status') == 'sst_and_checker_passed' and row.get('checked_ranks') == 64
               and row.get('container_exit') == 0 for row in rows), 'preceding_batch_not_passed'
    if previous == 4:
        baseline = SESSION_ROOT / 'run-b5383cf-a1' / 'cases'
        for phase in STAGES[:2]:
            row = json_file(baseline / f't4-{phase}-a1' / 'phase-result.json')
            assert row['candidate_commit'] == REVISIONS['b5383cf'] and row['tokens'] == 4
            assert row['stage'] == phase and row['status'] == 'sst_and_checker_passed' and row['checked_ranks'] == 64


def wait_for_bs16(root, revision, timeout):
    journal = root / 'hapi-logs/t16-batch-a1.json'
    setup_error = root / 't16-batch-a1-host-error.log'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert not setup_error.exists(), 'bs16_setup_failed'
        if journal.is_file():
            rows = json_file(journal)
            assert all(row.get('status') == 'sst_and_checker_passed' and row.get('container_exit') == 0
                       for row in rows), 'bs16_stage_failed'
            if len(rows) == len(STAGES):
                require_previous_bs(root, revision, 32)
                return
        time.sleep(15)
    raise TimeoutError('bs16_completion_not_observed')


def wait_for_bs64(root, revision, timeout):
    def verify(rows, stages, expected_revision, attempt):
        assert tuple(row['stage'] for row in rows) == stages
        assert all((row['candidate_commit'], row['tokens'], row['attempt']) == (expected_revision, 64, attempt)
                   and row['status'] == 'sst_and_checker_passed' and row['checked_ranks'] == 64
                   and row['profiling_enabled'] and row['profiling_dropped_records'] == 0
                   and row['routes_sha256'] == '5cf3a46d45e57912b6156c431069867bacc803c803307b288a043a33c3bc7c49'
                   for row in rows), 'bs64_stage_not_passed'

    previous = root.parent / 'run-86f802f-a1/hapi-logs/t64-batch-a2.json'
    verify(json_file(previous)[:3], STAGES[:3], REVISIONS['86f802f'], 2)
    send = root / 'cases/t64-inter-server-send-a4/phase-result.json'
    verify([json_file(send)], STAGES[3:4], revision, 4)

    journal = root / 'hapi-logs/t64-batch-a4.json'
    setup_error = root / 't64-batch-a4-host-error.log'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert not setup_error.exists(), 'bs64_setup_failed'
        if journal.is_file():
            rows = json_file(journal)
            verify(rows, STAGES[4:4 + len(rows)], revision, 4)
            if len(rows) == len(STAGES) - 4:
                return
        time.sleep(15)
    raise TimeoutError('bs64_completion_not_observed')


def run_matrix(root, cid, tools, config, args):
    matrix = []
    for size in (32, 64, 128):
        predecessor_attempt = 1 if size == 32 else config['attempt']
        require_previous_bs(root, config['revision'], size, predecessor_attempt)
        point = dict(config, action='batch', tokens=size)
        stages = dict(stage_timeouts=args['matrix_timeouts'][str(size)])
        results = run_batch(root, cid, tools, point, stages)
        matrix.extend(results)
        reviewed = results[-1]
        if reviewed['status'] != 'sst_and_checker_passed' or reviewed['container_exit'] != 0:
            break
    return matrix


def watch_stage(root, args):
    stage = args['through_stage']
    deadline = time.monotonic() + args['timeout']
    tokens, attempt = args['tokens'], args['attempt']
    log = root / f't{tokens}-batch-a{attempt}-host-error.log'
    while True:
        result = status(root, args)
        rows = result.get('batch', [])
        if rows and any(row.get('status') != 'sst_and_checker_passed' or row.get('container_exit') != 0
                        for row in rows):
            result['watch_status'] = 'stage_failed'
        elif any(row['stage'] == stage for row in rows):
            result['watch_status'] = 'stage_passed'
        elif log.is_file():
            result['watch_status'] = 'host_setup_failed'
        elif time.monotonic() >= deadline:
            result['watch_status'] = 'waiting_at_deadline'
        else:
            time.sleep(15)
            continue
        result.pop('build-summary', None)
        return result


def profile_build(root, cid, tools, timeout):
    log = root / 'profile-build.log'
    script = str(Path('/work') / tools.relative_to(root) / 'build_profile.py')
    argv = ['docker', 'exec', cid, 'timeout', '--kill-after=15', str(timeout),
            'bash', '-c', 'source /opt/sim/cann-env.sh; '
            'export PATH=/opt/sim/Ascend/cann-9.1.0/tools/bisheng_compiler/bin:'
            '/opt/sim/Ascend/cann-9.1.0/aarch64-linux/ccec_compiler/bin:$PATH; exec python3 "$1"', 'bash', script]
    with log.open('xb') as stream:
        code = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout + 25).returncode
    summary = root / 'profile-build/build-summary.json'
    report = json_file(summary) if summary.is_file() else {'all_compiled': False}
    report.update(stage='profile-build', container_exit=code, log=str(log))
    print(json.dumps(report, separators=(',', ':')))
    return 0 if report['all_compiled'] and code == 0 else 1


STOP_STALLED_INTER_SEND = """
import json
import os
from pathlib import Path
import signal
import time

case = Path('/work/cases/t64-inter-server-send-a2')
run = json.loads((case / 'run.json').read_text())
assert run['stage'] == 'inter-server-send' and run['tokens'] == 64 and run['attempt'] == 2
assert run['candidate_commit'] == '86f802f9f8a19728364c466b0e9cb7416e038595'
assert run['status'] == 'running' and run['profiling_enabled']
assert not (case / 'phase-result.json').exists()
log = case / 'sim.log'
first = log.stat()
assert time.time() - first.st_mtime >= 1800
process = Path('/proc') / str(run['pid'])
assert (process / 'cwd').resolve() == case
parent = int((process / 'stat').read_text().rsplit(')', 1)[1].split()[1])
runner = Path('/proc') / str(parent)
_, script, config = (runner / 'cmdline').read_bytes().rstrip(b'\\0').split(b'\\0')
args = json.loads(config)
assert script.endswith(b'/sim_case.py')
assert (args['action'], args['tokens'], args['attempt'], args['revision']) == (
    'inter-server-send', 64, 2, run['candidate_commit'])
assert time.time() + 300 < run['started_unix'] + args['timeout'], 'case_near_its_deadline'
time.sleep(12)
second = log.stat()
assert (first.st_mtime_ns, first.st_size) == (second.st_mtime_ns, second.st_size), 'case_log_advanced'
assert not (case / 'phase-result.json').exists() and (process / 'cwd').resolve() == case
assert (runner / 'cmdline').read_bytes().endswith(config + b'\\0'), 'owning_runner_changed'
os.kill(parent, signal.SIGTERM)
"""


def main(args):
    action, tokens, attempt, timeout = (args[key] for key in ('action', 'tokens', 'attempt', 'timeout'))
    candidate = args.get('candidate', '86f802f')
    revision = REVISIONS[candidate]
    root = SESSION_ROOT / f'run-{candidate}-a1'
    case = root / 'cases' / f't{tokens}-{action}-a{attempt}'
    if action in ('status', 'watch'):
        report = status(root, args) if action == 'status' else watch_stage(root, args)
        print(json.dumps(report, separators=(',', ':')))
        return 0
    try:
        if args.get('require_bs4_gateway'):
            assert action == 'batch' and tokens == 4 and candidate == '86f802f'
            deadline = time.monotonic() + 150
            require_bs4_gateway(deadline)
            bootstrap = root / 'bootstrap-result.json'
            while not bootstrap.is_file() and time.monotonic() < deadline:
                time.sleep(2)
            assert bootstrap.is_file(), 'candidate_build_result_pending'
            result = json_file(bootstrap)
            assert result['revision'] == revision and result['build_exit'] == 0, 'candidate_build_not_passed'
        cid = task_container(root, revision)
        assert json_file(root / 'build-summary.json')['all_compiled'], 'candidate_build_not_passed'
        if action == 'inter-evidence':
            tools, _ = stage_tools(root, args['files'])
            script = str(Path('/work') / tools.relative_to(root) / 'inter_snapshot.py')
            proc = subprocess.run(['docker', 'exec', cid, 'python3', script], capture_output=True, timeout=timeout)
            output = proc.stdout if proc.returncode == 0 else proc.stderr
            (root / 'inter-evidence.log').write_bytes(output)
            if proc.returncode:
                return proc.returncode
            print(proc.stdout.decode(), end='')
            return 0
        if action == 'matrix':
            wait_for_bs16(root, revision, args['wait_for_bs16'])
            if args.get('profiling'):
                profile_summary = root / 'profile-build/build-summary.json'
                deadline = time.monotonic() + 750
                while not profile_summary.is_file() and time.monotonic() < deadline:
                    time.sleep(5)
                assert profile_summary.is_file(), 'profile_build_result_pending'
                assert json_file(profile_summary)['all_compiled'], 'profile_build_not_passed'
        if action == 'inter-snapshot':
            prior_path = root / 'cases/t64-inter-server-send-a2/phase-result.json'
            interrupt = args.get('interrupt_stalled', False)
            if interrupt and not prior_path.is_file():
                proc = subprocess.run(['docker', 'exec', cid, 'python3', '-c', STOP_STALLED_INTER_SEND],
                                      capture_output=True, timeout=25)
                if proc.returncode:
                    print(json.dumps({'status': 'interruption_skipped',
                                      'detail': proc.stderr.decode(errors='replace').splitlines()[-1:]}))
                    return 0
            deadline = time.monotonic() + args['wait_for_terminal']
            while not prior_path.is_file() and time.monotonic() < deadline:
                time.sleep(10)
            prior = json_file(prior_path)
            expected_terminal = 'interrupted' if interrupt else 'timed_out'
            if prior['status'] != expected_terminal:
                print(json.dumps({'status': 'snapshot_skipped', 'original_status': prior['status']}))
                return 0
        if 'wait_for_bs64' in args:
            wait_for_bs64(root, revision, args['wait_for_bs64'])
        with (root / 'sst.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if args.get('require_previous_bs'):
                assert action == 'batch'
                require_previous_bs(root, revision, tokens)
            tools, platform_hashes = stage_tools(root, args['files'])
            if action == 'profile-build':
                return profile_build(root, cid, tools, timeout)
            config = dict(action=action, tokens=tokens, attempt=attempt, timeout=timeout,
                          revision=revision, platform_sha256=platform_hashes, profiling=args.get('profiling', False))
            if config['profiling']:
                profile_summary = root / 'profile-build/build-summary.json'
                assert json_file(profile_summary)['all_compiled'], 'profile_build_not_passed'
            if action == 'inter-snapshot':
                report = run_case(root, cid, tools, dict(config, action='inter-server-send',
                                                       diagnostic_stop_us=args['stop_at_us']))
                print(json.dumps(report, separators=(',', ':')))
                return 0 if report['status'] == 'diagnostic_snapshot' and report['container_exit'] == 0 else 1
            if action == 'matrix':
                results = run_matrix(root, cid, tools, config, args)
                report = dict(matrix=results, revision=revision)
            elif action == 'batch':
                results = run_batch(root, cid, tools, config, args)
                report = dict(batch=results, tokens=tokens, revision=revision)
            else:
                config['source_case'] = args.get('source_case')
                results = [run_case(root, cid, tools, config)]
                report = results[0]
        print(json.dumps(report, separators=(',', ':')))
        last = results[-1]
        return 0 if last['status'] == 'sst_and_checker_passed' and last['container_exit'] == 0 else 1
    except Exception as error:
        if root.is_dir():
            log = root / f'{case.name}-host-error.log'
            log.write_text(traceback.format_exc())
        report = {'status': 'host_setup_failed', 'error_class': type(error).__name__,
                  'detail': str(error)[:240], 'case': str(case)}
        print(json.dumps(report, separators=(',', ':')))
        return 1
