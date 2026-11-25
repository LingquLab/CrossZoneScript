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
        assert name in {'fixture.py', 'prefill.cpp', 'sim_case.py', 'return_case.py', 'addressed_exports.py'}
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
)


def run_case(root, cid, tools, config):
    action, tokens, attempt, timeout = (config[key] for key in ('action', 'tokens', 'attempt', 'timeout'))
    case = root / 'cases' / f't{tokens}-{action}-a{attempt}'
    logs = root / 'hapi-logs'
    logs.mkdir(exist_ok=True)
    log_path = logs / (case.name + '.log')
    container_tools = str(Path('/work') / tools.relative_to(root))
    argv = [
        'docker', 'exec', cid, 'timeout', '--kill-after=20', str(timeout + 230),
        'python3', f'{container_tools}/sim_case.py', json.dumps(config),
    ]
    with log_path.open('xb') as log:
        proc = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, timeout=timeout + 260)
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

def main(args):
    action, tokens, attempt, timeout = (args[key] for key in ('action', 'tokens', 'attempt', 'timeout'))
    candidate = args.get('candidate', '86f802f')
    revision = REVISIONS[candidate]
    root = SESSION_ROOT / f'run-{candidate}-a1'
    case = root / 'cases' / f't{tokens}-{action}-a{attempt}'
    if action == 'status':
        print(json.dumps(status(root, args), separators=(',', ':')))
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
        with (root / 'sst.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            tools, platform_hashes = stage_tools(root, args['files'])
            config = dict(action=action, tokens=tokens, attempt=attempt, timeout=timeout,
                          revision=revision, platform_sha256=platform_hashes)
            if action == 'batch':
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
