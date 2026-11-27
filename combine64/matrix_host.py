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

SESSION = '1a5bdfed-7c8c-47ca-b7b6-827c1777651a'
REVISION = 'c03090090b9abf3ccf3b5ce51fb3aaf3d9d3cd32'
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
    'final-independent', 'joint-final', 'joint-reuse-final',
)
REPORT_FIELDS = (
    'candidate_commit', 'stage', 'tokens', 'attempt', 'status', 'blocks', 'intra_cores', 'peer_slots',
    'inter_cores', 'port_jetties', 'port0_die_mode', 'gateway_reducers', 'final_cores',
    'routes_sha256', 'source_object_sha256', 'kernel_sha256',
    'environment_sha256', 'platform_simulated_us', 'sst_wall_seconds',
    'runner_wall_seconds', 'guards', 'checked_ranks', 'checked_bytes',
    'predecessor', 'actual_input_sha256', 'model_config', 'error',
    'profiling_enabled', 'profiling_operator_us', 'profiling_max_lane_ticks',
    'profiling_lanes', 'profiling_dropped_records', 'profiling_clock_receipt',
    'diagnostic_only', 'stop_at_us', 'diagnostic_source', 'lane_status_histogram', 'incomplete_lanes',
    'probe_histograms', 'probe_missing_ranks', 'readiness_trace', 'sst_threads',
    'joint_status', 'ready_markers', 'outstanding_sq', 'outstanding_sq_examples', 'cq_samples', 'cq_frontiers',
    'joint_trace', 'inter_trace', 'local_return_qp_base', 'returncode',
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


POINTS = ('local-8x1', 'local-4x2', 'inter-16x8', 'inter-16x16', 'joint-16x8', 'joint-16x16')


def point_settings(bs, point):
    if point.startswith('local'):
        return 'local-send', (8 if point == 'local-8x1' else 4), 1
    slots = 1 if point.endswith('16x8') else 2
    if point.startswith('inter'):
        return 'inter-server-send', 8, slots
    dedicated = (slots == 1) != (bs == 32)
    return ('joint-final' if dedicated else 'joint-reuse-final'), (4 if slots == 1 else 8), slots


def passed(report):
    return (report.get('status') == 'sst_and_checker_passed'
            and report.get('checked_ranks') == 64
            and report.get('port0_die_mode') == 'chip_topo'
            and report.get('profiling_dropped_records') == 0
            and all(report.get(key, 0) > 0 for key in
                    ('platform_simulated_us', 'profiling_operator_us', 'sst_wall_seconds', 'runner_wall_seconds')))


def ensure_profile_build(root, cid, tools):
    profile = root / 'profile-build'
    summary = profile / 'build-summary.json'
    if summary.is_file() and json_file(summary)['all_compiled']:
        return
    assert json_file(root / 'build-summary.json')['all_compiled'], 'production_build_incomplete'
    process_check = r"""
import os
from pathlib import Path
for process in Path('/proc').glob('[0-9]*'):
    if process.name == str(os.getpid()):
        continue
    try:
        arguments = (process / 'cmdline').read_bytes().split(b'\0')
    except (FileNotFoundError, ProcessLookupError):
        continue
    if any(argument.endswith((b'/build_profile.py', b'/build.sh', b'/sstsim.x', b'/sim_case.py'))
           for argument in arguments):
        raise SystemExit(1)
"""
    checked = subprocess.run(['docker', 'exec', cid, 'python3', '-c', process_check],
                             capture_output=True, timeout=20)
    assert checked.returncode == 0, 'container_has_active_build_or_simulation'
    if profile.exists():
        profile.rename(profile.with_name(f'profile-build-incomplete-{time.time_ns()}'))
    script = str(Path('/work') / tools.relative_to(root) / 'build_profile.py')
    command = ('source /opt/sim/cann-env.sh; '
               'export PATH=/opt/sim/Ascend/cann-9.1.0/tools/bisheng_compiler/bin:'
               '/opt/sim/Ascend/cann-9.1.0/aarch64-linux/ccec_compiler/bin:$PATH; '
               'exec python3 "$1"')
    with (root / f'profile-resume-{time.time_ns()}.log').open('xb') as log:
        result = subprocess.run(['docker', 'exec', cid, 'timeout', '--kill-after=15', '420',
                                 'bash', '-c', command, 'bash', script],
                                stdout=log, stderr=subprocess.STDOUT, timeout=455)
    assert result.returncode == 0 and json_file(summary)['all_compiled'], 'profile_build_failed'


def main(request):
    bs = request['bs']
    host = request.get('host', 'host-94-26')
    assert host in ('host-94-26', 'host-94-18')
    suffix = '-host18' if host == 'host-94-18' else ''
    root = SESSION_ROOT / f'run-{REVISION[:7]}{suffix}-a1'
    journal = root / 'matrix-results.json'
    cid = task_container(root, REVISION)
    if request['action'] == 'status':
        rows = json_file(journal) if journal.exists() else {}
        progress = root / 'progress.json'
        print(json.dumps({'results': rows, 'progress': json_file(progress) if progress.exists() else {}}))
        return 0
    points = request['points']
    rows = []
    with (root / 'matrix.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        results = json_file(journal) if journal.exists() else {}
        for earlier in (size for size in (4, 32) if size < bs):
            assert all(passed(results.get(f'{earlier}-{point}', {})) for point in POINTS), 'earlier_bs_incomplete'
        tools, platform_hashes = stage_tools(root, request['files'])
        if request['action'] == 'resume':
            ensure_profile_build(root, cid, tools)
        assert json_file(root / 'profile-build/build-summary.json')['all_compiled'], 'profile_build_incomplete'
        for point in points:
            name = f'{bs}-{point}'
            if passed(results.get(name, {})):
                rows.append(results[name])
                continue
            stage, intra, slots = point_settings(bs, point)
            attempt = (POINTS.index(point) + 1) + 10 * request.get('retry', 0)
            config = dict(action=stage, tokens=bs, attempt=attempt, timeout=request['timeout'],
                          revision=REVISION, profiling=True, platform_sha256=platform_hashes,
                          intra_cores=intra, peer_slots=slots, sst_threads=8)
            row = run_case(root, cid, tools, config)
            row.update(point=point, tokens=bs)
            results[name] = row
            temp = journal.with_suffix('.pending')
            temp.write_text(json.dumps(results, indent=2))
            temp.replace(journal)
            rows.append(row)
            if not passed(row):
                break
    print(json.dumps({'bs': bs, 'rows': rows}, separators=(',', ':')))
    return 0 if len(rows) == len(points) and all(passed(row) for row in rows) else 1
