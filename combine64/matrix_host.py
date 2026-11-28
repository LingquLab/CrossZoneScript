# Copyright (c) 2026 TileXR Project.
"""Check the task's container and launch one foreground SST case."""
import fcntl
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import time

SESSION = '598ffb8d-c53a-4736-b168-be6f3d0e706f'
REVISION = 'a50893f1544b251af517ea8e77da391ec2072950'
SESSION_ROOT = Path(f'/mnt/share/tilexr-sim-jobs/{SESSION}')
TEMPLATE = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549')
IMAGE = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
PLATFORM_FILES = {'test_UBusSim.py', 'sst-environment.json', 'environment.snapshot.json', 'installed_model.py'}

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
        assert name in {'fixture.py', 'prefill.cpp', 'sim_case.py', 'addressed_exports.py',
                        'profile_case.py', 'clock_evidence.json'}
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



POINTS = [(cores, chunk) for cores in (1, 2, 4, 8) for chunk in (16, 32, 64, 128)]
SUMMARY = ('candidate_commit', 'cores', 'chunk_tokens', 'chunks', 'groups', 'status', 'routes_sha256',
           'port0_die_mode', 'port0_die_histogram', 'source_object_sha256', 'kernel_sha256',
           'model_sha256', 'environment_sha256', 'send_rows', 'guards', 'checked_ranks',
           'platform_simulated_us', 'profiling_operator_us', 'profiling_lanes', 'profiling_dropped_records',
           'profiling_batches', 'profiling_cqes_unobserved', 'sst_wall_seconds', 'runner_wall_seconds',
           'profiling_clock_receipt', 'returncode', 'error')


def report_for(case):
    path = case / 'phase-result.json'
    if not path.is_file():
        path = case / 'run.json'
    if not path.is_file():
        return None
    row = json_file(path)
    return {**{key: row[key] for key in SUMMARY if key in row},
            'case': case.name, 'final': path.name == 'phase-result.json'}


def passed(row):
    return (row and row.get('status') == 'sst_and_checker_passed' and row.get('final')
            and row.get('candidate_commit') == REVISION and row.get('checked_ranks') == 64
            and row.get('port0_die_mode') == 'chip_topo' and row.get('port0_die_histogram') == {'0': 32, '1': 32}
            and row.get('profiling_dropped_records') == 0 and row.get('profiling_lanes') == 64 * row['cores']
            and row.get('guards') == 128
            and all(row.get(key, 0) > 0 for key in
                    ('platform_simulated_us', 'profiling_operator_us', 'sst_wall_seconds', 'runner_wall_seconds')))


def status(root):
    report = {'root_exists': root.is_dir()}
    for name in ('identity', 'bootstrap-result', 'progress'):
        path = root / (name + '.json')
        if path.is_file():
            data = json_file(path)
            if name == 'bootstrap-result':
                data = {key: data[key] for key in ('revision', 'image', 'environment_hashes', 'profile_exit',
                                                   'profile_build_seconds', 'profile') if key in data}
            elif name == 'identity':
                data = {key: data[key] for key in ('revision', 'image', 'platform_environment_sha256', 'container_id')
                        if key in data}
            report[name] = data
    report['rows'] = [row for case in sorted((root / 'cases').glob('cores*-chunk*-a*'))
                      if (row := report_for(case))]
    available = shutil.disk_usage(root if root.exists() else SESSION_ROOT.parent).free
    report['available_gib'] = round(available / 2**30, 2)
    cid = report.get('identity', {}).get('container_id')
    if cid:
        command = ['docker', 'inspect', '--format', '{{.State.Running}} {{.State.OOMKilled}}', cid]
        docker = subprocess.run(command, capture_output=True, timeout=15)
        report['docker_state'] = docker.stdout.decode('ascii', 'replace').split()
    return report


def run(root, cid, request):
    points = [tuple(point) for point in request['points']]
    assert points and len(set(points)) == len(points) and all(point in POINTS for point in points)
    assert type(request['timeout']) is int and 120 <= request['timeout'] <= 3600
    attempt = request.get('attempt', 1)
    assert type(attempt) is int and 1 <= attempt <= 20
    rows = []
    with (root / 'matrix.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        tools, platform_hashes = stage_tools(root, request['files'])
        script = str(Path('/work') / tools.relative_to(root) / 'sim_case.py')
        logs = root / 'hapi-logs'
        logs.mkdir(exist_ok=True)
        for cores, chunk in points:
            matches = sorted((root / 'cases').glob(f'cores{cores}-chunk{chunk}-a*'))
            existing = next((row for case in matches if passed(row := report_for(case))), None)
            if existing:
                rows.append(existing)
                continue
            name = f'cores{cores}-chunk{chunk}-a{attempt}'
            case = root / 'cases' / name
            assert not case.exists() and not case.with_name(case.name + '-asset').exists()
            timeout = request['timeout']
            config = dict(cores=cores, chunk=chunk, label=name, timeout=timeout,
                          revision=REVISION, platform_sha256=platform_hashes)
            with (logs / (name + '.log')).open('xb') as log:
                command = ['docker', 'exec', cid, 'timeout', '--kill-after=20', str(timeout + 1000),
                           'python3', script, json.dumps(config)]
                proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout + 1040)
            row = report_for(case) or {'case': name, 'status': 'runner_failed_before_result'}
            row['container_exit'] = proc.returncode
            rows.append(row)
            if not passed(row):
                break
            # The verified reports, profiling GM records and Perfetto trace stay in the case.
            for part in ('input_bin', 'fixture-data', 'bin'):
                shutil.rmtree(case / part)
            for path in (case / 'actual').glob('rank*/*.bin'):
                if path.name != 'profile.bin':
                    path.unlink()
    print(json.dumps({'rows': rows, 'completed': len(rows)}, separators=(',', ':')))
    return 0 if len(rows) == len(points) and all(passed(row) for row in rows) else 1


def main(request):
    host = request['host']
    assert host in ('host-94-26', 'host-94-18')
    assert subprocess.check_output(['hostname', '-s'], timeout=10).decode().strip() == host
    suffix = '-host18' if host == 'host-94-18' else ''
    root = SESSION_ROOT / f'run-{REVISION[:7]}{suffix}-a1'
    if request['action'] == 'status':
        print(json.dumps(status(root), separators=(',', ':')))
        return 0
    assert request['action'] == 'run'
    return run(root, task_container(root, REVISION), request)
