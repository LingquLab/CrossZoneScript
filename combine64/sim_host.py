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

SESSION = '44b9c060-a5dd-46a8-897f-e4a6fee171e2'
REVISIONS = {
    '518b9df': '518b9dfa02dc3d33852ebecea90143d569ea50af',
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
    'probe_histograms', 'probe_missing_ranks', 'readiness_trace',
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
    now = time.time()
    report = {'root': str(root), 'exists': root.is_dir(), 'unix': now}
    progress = root / 'progress.json'
    if progress.is_file():
        state = json_file(progress)
        state['age_seconds'] = round(now - state['updated_unix'], 1)
        report['progress'] = state
    tokens, attempt = args['tokens'], args['attempt']
    identity = root / 'identity.json'
    cid = None
    if identity.is_file():
        try:
            cid = task_container(root, REVISIONS[args['candidate']])
        except (AssertionError, KeyError, subprocess.SubprocessError):
            pass
    reports = []
    for stage in STAGES:
        case = root / 'cases' / f't{tokens}-{stage}-a{attempt}'
        path = case / 'phase-result.json'
        if path.is_file():
            run = json_file(path)
            reports.append({key: run[key] for key in REPORT_FIELDS if key in run})
        elif (case / 'run.json').is_file():
            run = json_file(case / 'run.json')
            row = {key: run[key] for key in REPORT_FIELDS if key in run}
            row['sst_elapsed_seconds'] = round(now - run['started_unix'], 1)
            log = case / 'sim.log'
            if log.is_file():
                st = log.stat()
                row.update(log_bytes=st.st_size, log_age_seconds=round(now - st.st_mtime, 1))
                with log.open('rb') as feed:
                    feed.seek(max(0, st.st_size - 524288))
                    tail = feed.read(524288).decode(errors='replace')
                for key, pattern in (
                    ('last_sim_cycle', r'Sim: ([0-9]+)[(]cycle[)]'),
                    ('last_cq_cycle', r'Sim: ([0-9]+)[(]cycle[)][^\n]*WrCQEResp'),
                ):
                    observed = re.findall(pattern, tail)
                    if observed:
                        row[key] = int(observed[-1])
            row['sst_has_exited'] = run.get('returncode') is not None
            pid = run.get('pid')
            if cid and type(pid) is int and not row['sst_has_exited']:
                proc = subprocess.run(['docker', 'exec', cid, 'cat', f'/proc/{pid}/stat'],
                                      capture_output=True, timeout=8)
                if proc.returncode == 0:
                    fields = proc.stdout.decode().rsplit(')', 1)[1].split()
                    row.update(process_state=fields[0], cpu_ticks=int(fields[11]) + int(fields[12]))
            reports.append(row)
    resource_update = root / 'container-memory.json'
    if resource_update.is_file():
        report['container_memory'] = json_file(resource_update)
    report['cases'] = reports
    return report

def run_batch(root, cid, tools, config, args):
    tokens, attempt = config['tokens'], config['attempt']
    logs = root / 'hapi-logs'
    logs.mkdir(exist_ok=True)
    start = STAGES.index(args.get('start_stage', STAGES[0]))
    journal = logs / f't{tokens}-batch-{STAGES[start]}-a{attempt}.json'
    end = STAGES.index(args.get('end_stage', STAGES[-1]))
    assert start <= end, 'invalid_stage_interval'
    reports = []
    with journal.open('x') as log:
        json.dump(reports, log)
    stage_timeouts = args.get('stage_timeouts', {})
    for action in STAGES[start:end + 1]:
        point = dict(config, action=action, timeout=stage_timeouts.get(action, config['timeout']))
        reports.append(run_case(root, cid, tools, point))
        pending = journal.with_suffix('.tmp')
        pending.write_text(json.dumps(reports, indent=2) + '\n')
        pending.replace(journal)
        if reports[-1]['status'] != 'sst_and_checker_passed' or reports[-1]['container_exit'] != 0:
            break
    return reports



def verify_prerequisite(root, revision, args):
    predecessor = args.get('after')
    if not predecessor:
        return
    assert set(predecessor) == {'tokens', 'stages', 'max_wait_seconds'}
    tokens = predecessor['tokens']
    assert tokens in (4, 32)
    assert predecessor['stages'].keys() <= set(STAGES)
    assert 60 <= predecessor['max_wait_seconds'] <= 7200
    deadline = time.monotonic() + predecessor['max_wait_seconds']
    for stage, attempt in predecessor['stages'].items():
        assert type(attempt) is int and 1 <= attempt <= 20
        path = root / 'cases' / f't{tokens}-{stage}-a{attempt}' / 'phase-result.json'
        while not path.is_file():
            assert time.monotonic() < deadline, f'prerequisite_missing_{tokens}_{stage}'
            time.sleep(5)
        row = json_file(path)
        assert (row['tokens'], row['stage'], row['attempt'], row['candidate_commit']) == (
            tokens, stage, attempt, revision)
        assert row['status'] == 'sst_and_checker_passed', f'prerequisite_failed_{tokens}_{stage}'
        assert row['checked_ranks'] == 64 and row['guards'] > 0
        assert row['profiling_lanes'] == 64 * row['blocks'] and row['profiling_dropped_records'] == 0


def retry_failed(root, revision, wait):
    deadline = time.monotonic() + wait
    path = root / 'cases/t4-local-return-a3/phase-result.json'
    while not path.is_file():
        assert time.monotonic() < deadline, 'return_retry_result_pending'
        time.sleep(2)
    result = json_file(path)
    assert result['candidate_commit'] == revision and result['attempt'] == 3
    if result['status'] == 'sst_and_checker_passed':
        print(json.dumps({'status': 'probe_skipped', 'case': str(path.parent)}))
        return False
    assert result['status'] in ('failed', 'timed_out', 'interrupted')
    return True


def set_container_memory(cid, gib):
    assert gib == 64
    desired = gib * 2**30
    free_kib = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                    if line.startswith('MemAvailable:'))
    assert free_kib * 1024 > 4 * desired
    def limit():
        return int(subprocess.check_output(
            ['docker', 'inspect', '--format', '{{.HostConfig.Memory}}', cid], timeout=10))
    previous = limit()
    if previous != desired:
        assert previous == 32 * 2**30
        subprocess.run(['docker', 'update', '--memory', f'{gib}g', '--memory-swap', f'{gib}g', cid],
                       capture_output=True, check=True, timeout=20)
    actual = limit()
    assert actual == desired
    return dict(before_bytes=previous, after_bytes=actual, host_available_bytes=free_kib * 1024)


def profile_build(root, cid, tools, timeout, ready_probe=False):
    mode = 'ready-probe-build' if ready_probe else 'profile-build'
    log = root / (mode + '.log')
    script = str(Path('/work') / tools.relative_to(root) / 'build_profile.py')
    argv = ['docker', 'exec', cid, 'timeout', '--kill-after=15', str(timeout),
            'bash', '-c', 'source /opt/sim/cann-env.sh; '
            'export PATH=/opt/sim/Ascend/cann-9.1.0/tools/bisheng_compiler/bin:'
            '/opt/sim/Ascend/cann-9.1.0/aarch64-linux/ccec_compiler/bin:$PATH; exec python3 "$@"', 'bash', script]
    if ready_probe:
        argv.append('ready-probe')
    with log.open('xb') as stream:
        code = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout + 25).returncode
    summary = root / mode / 'build-summary.json'
    report = json_file(summary) if summary.is_file() else {'all_compiled': False}
    report.update(stage='profile-build', container_exit=code, log=str(log))
    if not ready_probe or code:
        print(json.dumps(report, separators=(',', ':')))
    return 0 if report['all_compiled'] and code == 0 else 1


def main(args):
    action, tokens, attempt, timeout = (args[key] for key in ('action', 'tokens', 'attempt', 'timeout'))
    candidate = args['candidate']
    revision = REVISIONS[candidate]
    root = SESSION_ROOT / f'run-{candidate}-a1'
    case = root / 'cases' / f't{tokens}-{action}-a{attempt}'
    if action == 'status':
        print(json.dumps(status(root, args), separators=(',', ':')))
        return 0
    try:
        cid = task_container(root, revision)
        assert json_file(root / 'build-summary.json')['all_compiled'], 'candidate_build_not_passed'
        verify_prerequisite(root, revision, args)
        if args.get('ready_probe') and not retry_failed(root, revision, args['wait_for_idle_seconds']):
            return 0
        with (root / 'sst.lock').open('a') as lock:
            # The predecessor can still be returning its journal after writing a case result.
            wait_seconds = args.get('wait_for_idle_seconds', 30)
            deadline = time.monotonic() + wait_seconds
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if (not args.get('after') and 'wait_for_idle_seconds' not in args) or time.monotonic() >= deadline:
                        raise
                    time.sleep(1)
            memory = None
            if 'memory_gib' in args:
                memory = set_container_memory(cid, args['memory_gib'])
                (root / 'container-memory.json').write_text(json.dumps(memory))
            tools, platform_hashes = stage_tools(root, args['files'])
            if action == 'profile-build':
                return profile_build(root, cid, tools, timeout)
            config = dict(action=action, tokens=tokens, attempt=attempt, timeout=timeout, revision=revision,
                          platform_sha256=platform_hashes, profiling=args.get('profiling', True))
            if config['profiling']:
                assert json_file(root / 'profile-build/build-summary.json')['all_compiled']
            if args.get('ready_probe'):
                built = profile_build(root, cid, tools, 180, ready_probe=True)
                if built:
                    return built
                config['ready_probe'] = True
            if action == 'batch':
                results = run_batch(root, cid, tools, config, args)
                report = dict(batch=results, tokens=tokens, revision=revision)
            else:
                config['source_case'] = args.get('source_case')
                config['diagnostic_stop_us'] = args.get('stop_at_us')
                results = [run_case(root, cid, tools, config)]
                report = results[0]
        if memory is not None:
            report['container_memory'] = memory
        print(json.dumps(report, separators=(',', ':')))
        last = results[-1]
        expected = 'diagnostic_snapshot' if 'stop_at_us' in args else 'sst_and_checker_passed'
        return 0 if last['status'] == expected and last['container_exit'] == 0 else 1
    except Exception as error:
        if root.is_dir():
            log = root / f'{case.name}-host-error.log'
            log.write_text(traceback.format_exc())
        report = {'status': 'host_setup_failed', 'error_class': type(error).__name__,
                  'detail': str(error)[:240], 'case': str(case)}
        print(json.dumps(report, separators=(',', ':')))
        return 1
