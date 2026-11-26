# Copyright (c) 2026 TileXR Project.
"""Check the task's container and launch one foreground SST case."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
import traceback

SESSION = '44b9c060-a5dd-46a8-897f-e4a6fee171e2'
REVISIONS = {
    '518b9df': '518b9dfa02dc3d33852ebecea90143d569ea50af',
    'd04fde0': 'd04fde0c3d7b3e9017ca8d43383b9183fd7a2354',
    '8e005fa': '8e005faf781c14dab3c0e977b52741ee29b597ac',
    '85d4b8d': '85d4b8de182b0c813d586f7278920489c0c5c4a6',
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
    source_root = Path(identity.get('source_root', root))
    assert mounts['/work/source']['Source'] == str(source_root / 'source')
    assert mounts['/work/source']['RW'] is False
    if source_root != root:
        for folder in ('build-fp32', 'profile-build'):
            mount = mounts[f'/work/{folder}']
            assert mount['Source'] == str(source_root / folder) and not mount['RW']
    return cid


def prepare_variant(root, origin, revision, intra_cores, peer_slots):
    origin_identity = json_file(origin / 'identity.json')
    assert origin_identity['revision'] == revision and origin_identity['image'] == IMAGE
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:')) * 1024
    assert available >= 256 * 2**30 and len(os.sched_getaffinity(0)) >= 32, 'simulator_host_capacity'
    root.mkdir(parents=True, exist_ok=False)
    for folder in ('source', 'profile-build', 'build-fp32'):
        (root / folder).mkdir()
    shutil.copyfile(origin / 'build-summary.json', root / 'build-summary.json')
    name = f'combine-ht-{SESSION[:8]}-{revision[:7]}-i{intra_cores}-p{peer_slots}'
    argv = ['docker', 'run', '-dt', '--name', name, '--runtime=runc', '--network=none',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=8', '--memory=64g', '--memory-swap=64g',
            '--pids-limit=1024', '--shm-size=64m', '--ulimit', 'nofile=524288:524288',
            '--label', 'crosszone.session=' + SESSION, '--mount', f'type=bind,src={root},dst=/work']
    for folder in ('source', 'profile-build', 'build-fp32'):
        argv += ['--mount', f'type=bind,src={origin / folder},dst=/work/{folder},readonly']
    argv += [IMAGE, '/bin/bash', '-c', 'exec tail -f /dev/null']
    cid = subprocess.check_output(argv, timeout=40).decode().strip()
    identity = dict(revision=revision, image=IMAGE, container_id=cid, container_name=name,
                    source_root=str(origin), root=str(root), intra_cores=intra_cores, peer_slots=peer_slots)
    (root / 'identity.json').write_text(json.dumps(identity))
    return task_container(root, revision)


def stage_tools(root, files):
    key = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]
    tools = root / 'tools' / key
    tools.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        assert name in {'fixture.py', 'prefill.cpp', 'sim_case.py', 'return_case.py', 'addressed_exports.py',
                        'build_profile.py', 'profile_capture.h', 'profile_case.py', 'clock_evidence.json',
                        'inter_snapshot.py', 'joint_evidence.py'}
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
    'final-independent', 'joint-final',
)
REPORT_FIELDS = (
    'candidate_commit', 'stage', 'tokens', 'attempt', 'status', 'blocks', 'intra_cores', 'peer_slots',
    'routes_sha256', 'source_object_sha256', 'kernel_sha256',
    'environment_sha256', 'platform_simulated_us', 'sst_wall_seconds',
    'runner_wall_seconds', 'guards', 'checked_ranks', 'checked_bytes',
    'predecessor', 'actual_input_sha256', 'model_config', 'error',
    'profiling_enabled', 'profiling_operator_us', 'profiling_max_lane_ticks',
    'profiling_lanes', 'profiling_dropped_records', 'profiling_clock_receipt',
    'diagnostic_only', 'stop_at_us', 'diagnostic_source', 'lane_status_histogram', 'incomplete_lanes',
    'probe_histograms', 'probe_missing_ranks', 'readiness_trace', 'sst_threads',
    'joint_status', 'ready_markers', 'outstanding_sq', 'outstanding_sq_examples', 'cq_samples',
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


def log_state(log):
    if not log.is_file():
        return {}
    summary = {}
    st = log.stat()
    summary.update(log_bytes=st.st_size, log_age_seconds=round(time.time() - st.st_mtime, 1))
    with log.open('rb') as feed:
        feed.seek(max(0, st.st_size - 524288))
        tail = feed.read(524288).decode(errors='replace')
    for key, pattern in (
        ('last_sim_cycle', r'Sim: ([0-9]+)[(]cycle[)]'),
        ('last_cq_cycle', r'Sim: ([0-9]+)[(]cycle[)][^\n]*WrCQEResp'),
    ):
        observed = re.findall(pattern, tail)
        if observed:
            summary[key] = int(observed[-1])
    summary['simulation_complete_marker'] = 'Simulation is complete, simulated time:' in tail
    return summary


def status(root, args):
    report = {'root': str(root), 'exists': root.is_dir(), 'unix': time.time()}
    progress = root / 'progress.json'
    if progress.is_file():
        state = json_file(progress)
        state['age_seconds'] = round(time.time() - state['updated_unix'], 1)
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
            row = {key: run[key] for key in REPORT_FIELDS if key in run}
            if run['status'] != 'sst_and_checker_passed':
                row.update(log_state(case / 'sim.log'))
            reports.append(row)
        elif (case / 'run.json').is_file():
            run = json_file(case / 'run.json')
            row = {key: run[key] for key in REPORT_FIELDS if key in run}
            row['sst_elapsed_seconds'] = round(time.time() - run['started_unix'], 1)
            row.update(log_state(case / 'sim.log'))
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
    end = STAGES.index(args.get('end_stage', STAGES[-1]))
    stages = args.get('stages', STAGES[start:end + 1])
    assert stages and list(stages) == [stage for stage in STAGES if stage in stages]
    journal = logs / f't{tokens}-batch-{stages[0]}-a{attempt}.json'
    reports = []
    with journal.open('x') as log:
        json.dump(reports, log)
    stage_timeouts = args.get('stage_timeouts', {})
    for action in stages:
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


INTERRUPT_CASE = r"""
import json
import os
from pathlib import Path
import signal
import sys

expected = sys.argv[1]
stage = sys.argv[2]
assert stage in ('joint-producer', 'joint-final')
case = Path('/work/cases') / f't4-{stage}-a1'
if (case / 'phase-result.json').is_file() or not (case / 'run.json').is_file():
    raise SystemExit(0)
run = json.loads((case / 'run.json').read_text())
assert (run['candidate_commit'], run['tokens'], run['stage'], run['attempt'], run['status']) == (
    expected, 4, stage, 1, 'running')
if run.get('returncode') is not None:
    raise SystemExit(0)
proc = Path('/proc') / str(run['pid'])
if not proc.exists():
    raise SystemExit(0)
assert (proc / 'cwd').resolve() == case
parent = int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[1])
runner = Path('/proc') / str(parent)
cmd = (runner / 'cmdline').read_bytes().rstrip(bytes(1)).split(bytes(1))
assert len(cmd) == 3 and cmd[1].endswith(b'/sim_case.py')
args = json.loads(cmd[2])
assert tuple(args[key] for key in ('revision', 'tokens', 'action', 'attempt')) == (
    expected, 4, stage, 1)
os.kill(parent, signal.SIGTERM)
"""


def wait_or_interrupt_legacy(root, cid, revision):
    for stage in ('inter-server-send', 'final-independent'):
        deadline = time.monotonic() + 700
        path = root / 'cases' / f't4-{stage}-a1' / 'phase-result.json'
        while not path.is_file():
            assert time.monotonic() < deadline, f'prerequisite_missing_{stage}'
            time.sleep(3)
        verify_completed(json_file(path), revision, 4, stage, 8, 1)
    deadline = time.monotonic() + 180
    producer = root / 'cases/t4-joint-producer-a1'
    result = producer / 'phase-result.json'
    while not result.is_file() and time.monotonic() < deadline:
        if (producer / 'run.json').is_file():
            subprocess.run(['docker', 'exec', cid, 'python3', '-c', INTERRUPT_CASE,
                            revision, 'joint-producer'],
                           capture_output=True, timeout=20, check=True)
            break
        time.sleep(2)


def interrupt_stalled_joint(root, cid, revision, cycle):
    case = root / 'cases/t4-joint-final-a1'
    result = case / 'phase-result.json'
    if result.is_file():
        return
    first = log_state(case / 'sim.log')
    assert first['last_cq_cycle'] == cycle and first['log_age_seconds'] >= 300, 'joint_case_progressing'
    time.sleep(6)
    second = log_state(case / 'sim.log')
    assert second['last_cq_cycle'] == cycle and second['log_bytes'] == first['log_bytes']
    assert second['log_age_seconds'] >= first['log_age_seconds'] and not second['simulation_complete_marker']
    subprocess.run(['docker', 'exec', cid, 'python3', '-c', INTERRUPT_CASE, revision, 'joint-final'],
                   capture_output=True, timeout=20, check=True)
    deadline = time.monotonic() + 90
    while not result.is_file():
        assert time.monotonic() < deadline, 'joint_interrupt_pending'
        time.sleep(2)


def set_container_cpus(cid, count):
    assert count in (16, 32) and len(os.sched_getaffinity(0)) >= 4 * count
    subprocess.run(['docker', 'update', '--cpus', str(count), cid],
                   capture_output=True, check=True, timeout=20)
    actual = subprocess.check_output(['docker', 'inspect', '--format', '{{.HostConfig.NanoCpus}}', cid], timeout=10)
    assert int(actual) == count * 1_000_000_000


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


def validation_variants(origin, candidate):
    return [
        (8, 1, origin, STAGES),
        (4, 1, SESSION_ROOT / f'run-{candidate}-i4-p1', ('local-send', 'joint-final')),
        (4, 2, SESSION_ROOT / f'run-{candidate}-i4-p2', ('joint-final',)),
        (8, 2, SESSION_ROOT / f'run-{candidate}-i8-p2', ('inter-server-send', 'joint-final')),
    ]


def verify_completed(row, revision, tokens, stage, intra_cores, peer_slots):
    assert (row['candidate_commit'], row['tokens'], row['stage']) == (revision, tokens, stage)
    assert (row.get('intra_cores', 8), row.get('peer_slots', 1)) == (intra_cores, peer_slots)
    assert row['status'] == 'sst_and_checker_passed'
    assert row['checked_ranks'] == 64 and row['guards'] > 0
    assert row['profiling_lanes'] == 64 * row['blocks'] and row['profiling_dropped_records'] == 0


def run_variant_cases(origin, revision, variant, tokens, args):
    intra, slots, root, stages = variant
    if root != origin and not root.exists():
        cid = prepare_variant(root, origin, revision, intra, slots)
    else:
        cid = task_container(root, revision)
    point = dict(args, tokens=tokens, stages=stages)
    threads = args.get('sst_threads', 8)
    with (root / 'sst.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if threads != 8:
            set_container_cpus(cid, threads)
        tools, platform = stage_tools(root, args['files'])
        config = dict(tokens=tokens, attempt=1, timeout=args['timeout'], revision=revision,
                      profiling=True, intra_cores=intra, peer_slots=slots, platform_sha256=platform,
                      sst_threads=threads)
        rows = run_batch(root, cid, tools, config, point)
    assert len(rows) == len(stages)
    for stage, row in zip(stages, rows):
        assert row['container_exit'] == 0
        verify_completed(row, revision, tokens, stage, intra, slots)
    return rows


def run_bs4_joint_remainder(origin, revision, args):
    base, intra4, known, inter2 = validation_variants(origin, args['candidate'])
    intra, slots, root, stages = known
    verify_completed(json_file(root / 'cases/t4-joint-final-a1/phase-result.json'),
                     revision, 4, stages[0], intra, slots)

    def run_base_joint():
        gate = dict(args, after=dict(tokens=4, stages={stage: 1 for stage in STAGES[:-1]},
                                    max_wait_seconds=args['independent_wait_seconds']))
        verify_prerequisite(origin, revision, gate)
        time.sleep(2)
        return run_variant_cases(origin, revision, (*base[:3], ('joint-final',)), 4, args)

    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = [pool.submit(run_base_joint)]
        pending.extend(pool.submit(run_variant_cases, origin, revision, variant, 4, args)
                       for variant in (intra4, inter2))
        rows = [row for future in pending for row in future.result()]
    print(json.dumps(dict(batch=rows, revision=revision), separators=(',', ':')))
    return 0


def run_large_matrix(origin, revision, args):
    variants = validation_variants(origin, args['candidate'])
    for intra, slots, root, stages in variants:
        for stage in stages:
            report = json_file(root / 'cases' / f't4-{stage}-a1' / 'phase-result.json')
            verify_completed(report, revision, 4, stage, intra, slots)

    results = []
    for tokens in (32, 128):
        point = dict(args, stage_timeouts=args['matrix_timeouts'][str(tokens)])
        with ThreadPoolExecutor(max_workers=len(variants)) as pool:
            pending = [pool.submit(run_variant_cases, origin, revision, variant, tokens, point)
                       for variant in variants]
            results.extend(row for future in pending for row in future.result())
    print(json.dumps(dict(batch=results, revision=revision), separators=(',', ':')))
    return 0


def inspect_joint_snapshot(root, cid, args):
    tools, _ = stage_tools(root, args['files'])
    entry = Path('/work') / tools.relative_to(root) / 'joint_evidence.py'
    case = '/work/cases/t4-joint-final-a2'
    proc = subprocess.run(['docker', 'exec', cid, 'timeout', '125', 'python3', str(entry), case],
                          capture_output=True, timeout=145)
    (root / 'joint-evidence.stderr').write_bytes(proc.stderr)
    if proc.returncode == 0:
        print(json.dumps(json.loads(proc.stdout), separators=(',', ':')))
    return proc.returncode


def main(args):
    action, tokens, attempt, timeout = (args[key] for key in ('action', 'tokens', 'attempt', 'timeout'))
    candidate = args['candidate']
    revision = REVISIONS[candidate]
    origin = SESSION_ROOT / f'run-{candidate}-a1'
    intra_cores, peer_slots = args.get('intra_cores', 8), args.get('peer_slots', 1)
    root = origin if (intra_cores, peer_slots) == (8, 1) else SESSION_ROOT / f'run-{candidate}-i{intra_cores}-p{peer_slots}'
    case = root / 'cases' / f't{tokens}-{action}-a{attempt}'
    if action == 'status':
        if args.get('all_variants'):
            variants = [(8, 1, origin)]
            variants.extend((i, p, SESSION_ROOT / f'run-{candidate}-i{i}-p{p}') for i, p in ((4, 1), (4, 2), (8, 2)))
            reports = [dict(status(path, args), intra_cores=i, peer_slots=p) for i, p, path in variants]
            result = dict(variants=reports, available_cpus=len(os.sched_getaffinity(0)),
                          load_average=os.getloadavg())
        else:
            result = status(root, args)
        print(json.dumps(result, separators=(',', ':')))
        return 0
    try:
        if action == 'bs4-remainder':
            return run_bs4_joint_remainder(origin, revision, args)
        if action == 'matrix':
            return run_large_matrix(origin, revision, args)
        if root != origin and not root.exists():
            cid = prepare_variant(root, origin, revision, intra_cores, peer_slots)
        else:
            cid = task_container(root, revision)
        assert json_file(root / 'build-summary.json')['all_compiled'], 'candidate_build_not_passed'
        if action == 'joint-evidence':
            return inspect_joint_snapshot(root, cid, args)
        verify_prerequisite(root, revision, args)
        if args.get('replace_legacy_batch'):
            wait_or_interrupt_legacy(root, cid, revision)
        if 'stalled_cq_cycle' in args:
            interrupt_stalled_joint(root, cid, revision, args['stalled_cq_cycle'])
        if args.get('ready_probe') and not retry_failed(root, revision, args['wait_for_idle_seconds']):
            return 0
        with (root / 'sst.lock').open('a') as lock:
            # The predecessor can still be returning its journal after writing a case result.
            wait_seconds = 180 if args.get('replace_legacy_batch') else args.get('wait_for_idle_seconds', 30)
            deadline = time.monotonic() + wait_seconds
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if (not args.get('after') and 'wait_for_idle_seconds' not in args and 'stalled_cq_cycle' not in args
                        and not args.get('replace_legacy_batch')) or time.monotonic() >= deadline:
                        raise
                    time.sleep(1)
            memory = None
            if 'memory_gib' in args:
                memory = set_container_memory(cid, args['memory_gib'])
                (root / 'container-memory.json').write_text(json.dumps(memory))
            threads = args.get('sst_threads', 8)
            if threads != 8:
                set_container_cpus(cid, threads)
            tools, platform_hashes = stage_tools(root, args['files'])
            if action == 'profile-build':
                return profile_build(root, cid, tools, timeout)
            config = dict(action=action, tokens=tokens, attempt=attempt, timeout=timeout, revision=revision,
                          platform_sha256=platform_hashes, profiling=args.get('profiling', True),
                          intra_cores=args.get('intra_cores', 8), peer_slots=args.get('peer_slots', 1), sst_threads=threads)
            if config['profiling']:
                assert json_file(origin / 'profile-build/build-summary.json')['all_compiled']
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
                config['cq_probe'] = args.get('cq_probe', False)
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
