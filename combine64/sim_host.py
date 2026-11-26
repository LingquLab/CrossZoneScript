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

SESSION = '59af0a93-b62f-4211-927e-a44f047667cf'
REVISIONS = {'71b62db': '71b62db945c23268df029ecb6a4e8809a7579222',
             '2dfd2b4': '2dfd2b47394d013164f9270b445aa0bd39321755',
             'b61cb53': 'b61cb530b0d25d1e9ad18748ee7aeb7a7962740e'}
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
    'final-independent', 'joint-producer', 'final-from-producer', 'joint-reuse',
)
REPORT_FIELDS = (
    'world', 'hidden', 'topk', 'experts', 'seed', 'dtype', 'sim_send', 'final_cores',
    'final_pipeline_mode', 'dedicated_final_cores', 'reused_final_cores',
    'candidate_commit', 'stage', 'tokens', 'attempt', 'status', 'blocks',
    'routes_sha256', 'source_object_sha256', 'kernel_sha256',
    'environment_sha256', 'platform_simulated_us', 'sst_wall_seconds',
    'runner_wall_seconds', 'sst_started_utc', 'sst_finished_utc', 'inter_server_rank_send', 'guards', 'checked_ranks', 'checked_bytes',
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


def main(args):
    candidate = args.get('candidate', '71b62db')
    revision = REVISIONS[candidate]
    root = SESSION_ROOT / f'run-{candidate}-a1'
    action = args['action']
    tokens, attempt, timeout = (args[key] for key in ('tokens', 'attempt', 'timeout'))
    try:
        cid = task_container(root, revision)
        if action == 'status':
            report = dict(root=str(root), container_running=True)
            path = root / 'progress.json'
            if path.is_file():
                report['progress'] = json_file(path)
            reports = []
            for path in sorted((root/'cases').glob('*/phase-result.json')):
                row = json_file(path)
                reports.append({key: row[key] for key in REPORT_FIELDS if key in row})
            report['cases'] = reports
            progress = report.get('progress', {})
            if progress.get('stage') == 'sst':
                run = json_file(root/'cases'/progress['case']/'run.json')
                probe = subprocess.run(['docker','exec',cid,'test','-d',f"/proc/{run['pid']}"],capture_output=True,timeout=10)
                report['active_case_process_exists'] = probe.returncode == 0
                report['active_case_elapsed_seconds'] = round(time.time()-run['started_unix'],3)
            print(json.dumps(report,separators=(',',':')))
            return 0
        assert action in ('inter-server-send', 'joint-reuse')
        if action == 'joint-reuse':
            assert candidate in ('2dfd2b4','b61cb53'), 'candidate_has_no_reuse_mode'
        assert json_file(root/'profile-build/build-summary.json')['inter_server_rank_send'] is True
        with (root/'sst.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            tools, platform_hashes = stage_tools(root,args['files'])
            config=dict(action=action,tokens=tokens,attempt=attempt,timeout=timeout,
                        revision=revision,platform_sha256=platform_hashes,profiling=True)
            report=run_case(root,cid,tools,config)
        print(json.dumps(report,separators=(',',':')))
        return 0 if report['status']=='sst_and_checker_passed' and report['container_exit']==0 else 1
    except Exception as error:
        if root.is_dir():
            (root/f'{action}-t{tokens}-a{attempt}-host-error.log').write_text(traceback.format_exc())
        print(json.dumps(dict(status='host_setup_failed',error_class=type(error).__name__,detail=str(error)[:240]),separators=(',',':')))
        return 1
