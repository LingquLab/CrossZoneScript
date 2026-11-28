# Copyright (c) 2026 TileXR Project.
"""Check the task's container and launch one foreground SST case."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import os
import re
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
                        'profile_case.py', 'clock_evidence.json', 'recover_profile.py'}
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
           'profiling_clock_receipt', 'returncode', 'error', 'explicit_pipe', 'variant', 'profiling_detail', 'recovery_check_wall_seconds')


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
    report['parallel_progress'] = [json_file(path) for path in sorted(root.glob('progress-core*.json'))]
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


def run_points(root, cid, script, platform_hashes, points, timeout, attempt, lock_name, progress_file, explicit_pipe_points, variant):
    rows = []
    with (root / lock_name).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
            config = dict(cores=cores, chunk=chunk, label=name, timeout=timeout,
                          revision=REVISION, platform_sha256=platform_hashes,
                          progress_file=progress_file, explicit_pipe=(cores, chunk) in explicit_pipe_points,
                          variant='entry-pipe' if (cores, chunk) in explicit_pipe_points else variant)
            with (logs / (name + '.log')).open('xb') as log:
                command = ['docker', 'exec', cid, 'timeout', '--kill-after=20', str(timeout + 1000),
                           'python3', script, json.dumps(config)]
                proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout + 1040)
            row = report_for(case) or {'case': name, 'status': 'runner_failed_before_result'}
            row['container_exit'] = proc.returncode
            rows.append(row)
            if not passed(row):
                continue
            # The verified reports, profiling GM records and Perfetto trace stay in the case.
            for part in ('input_bin', 'fixture-data', 'bin'):
                shutil.rmtree(case / part)
            for path in (case / 'actual').glob('rank*/*.bin'):
                if path.name != 'profile.bin':
                    path.unlink()
    return rows


@contextmanager
def worker_container(root, core, memory_gib):
    name = f'combine-ht-{SESSION[:8]}-core{core}'
    source = root / 'source'
    command = ['docker', 'run', '--rm', '-dt', '--name', name, '--runtime=runc', '--network=none',
               '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=8',
               f'--memory={memory_gib}g', f'--memory-swap={memory_gib}g', '--pids-limit=1024', '--shm-size=64m',
               '--ulimit', 'nofile=524288:524288', '--label', 'crosszone.session=' + SESSION,
               '--mount', f'type=bind,src={root},dst=/work',
               '--mount', f'type=bind,src={source},dst=/work/source,readonly',
               IMAGE, '/bin/bash', '-c', 'exec tail -f /dev/null']
    cid = subprocess.check_output(command, timeout=45).decode('ascii').strip()
    try:
        details = json.loads(subprocess.check_output(['docker', 'inspect', cid], timeout=20))[0]
        assert details['Id'] == cid and details['Name'] == '/' + name and details['Image'] == IMAGE
        assert details['Config']['Labels']['crosszone.session'] == SESSION
        assert details['State']['Running'] and details['HostConfig']['NetworkMode'] == 'none'
        assert details['HostConfig']['Memory'] == memory_gib * 2**30
        yield cid
    finally:
        subprocess.run(['docker', 'stop', '--time', '20', cid], capture_output=True, check=True, timeout=40)


def run(root, cid, request):
    points = [tuple(point) for point in request['points']]
    assert points and len(set(points)) == len(points) and all(point in POINTS for point in points)
    variant = request.get('variant', 'baseline')
    assert variant in ('baseline', 'inline-post', 'fixed-events', 'timing-only')
    variant_builds = {'inline-post': 'profile-inline-build', 'fixed-events': 'profile-fixed-events-build',
                      'timing-only': 'profile-timing-build'}
    if variant in variant_builds:
        assert json_file(root / variant_builds[variant] / 'build-summary.json')['all_compiled']
    timeout = request['timeout']
    assert type(timeout) is int and 120 <= timeout <= 3600
    attempt = request.get('attempt', 1)
    assert type(attempt) is int and 1 <= attempt <= 20
    explicit_pipe_points = {tuple(point) for point in request.get('explicit_pipe_points', [])}
    assert explicit_pipe_points <= set(points)
    if explicit_pipe_points:
        assert json_file(root / 'profile-pipe-build/build-summary.json')['all_compiled']
    with (root / 'stage.lock').open('a') as stage:
        fcntl.flock(stage, fcntl.LOCK_EX)
        tools, platform_hashes = stage_tools(root, request['files'])
    script = str(Path('/work') / tools.relative_to(root) / 'sim_case.py')
    if request.get('parallel'):
        groups = {core: [point for point in points if point[0] == core] for core in sorted({p[0] for p in points})}
        assert set(groups) <= ({1, 2, 4} if variant in ('fixed-events', 'timing-only') else {2, 4, 8}) and len(groups) <= 3
        if variant in ('fixed-events', 'timing-only'):
            assert all(not any(case_dir.name.startswith(f'cores{core}-chunk{chunk}-a') and
                               case_dir.name in request.get('active_cases', [])
                               for case_dir in (root / 'cases').iterdir()) for core, chunk in points)
        with open('/proc/meminfo') as memory:
            available_kib = next(int(line.split()[1]) for line in memory if line.startswith('MemAvailable:'))
        assert len(os.sched_getaffinity(0)) >= 8 * (len(groups) + 1)
        memory_gib = request.get('memory_gib', 64)
        assert memory_gib in (64, 128)
        assert available_kib >= (memory_gib * len(groups) + 160) * 1024**2

        def run_group(core, group):
            name = f'{core}-{variant}' if variant in ('fixed-events', 'timing-only') else core
            with worker_container(root, name, memory_gib) as worker:
                return run_points(root, worker, script, platform_hashes, group, timeout, attempt,
                                  f'core{name}.lock', f'progress-core{name}.json', explicit_pipe_points, variant)

        with ThreadPoolExecutor(max_workers=len(groups)) as executor:
            tasks = [executor.submit(run_group, core, group) for core, group in groups.items()]
            rows = [row for task in tasks for row in task.result()]
    else:
        rows = run_points(root, cid, script, platform_hashes, points, timeout, attempt,
                          'matrix.lock', 'progress.json', explicit_pipe_points, variant)
    print(json.dumps({'rows': rows, 'completed': len(rows)}, separators=(',', ':')))
    return 0 if len(rows) == len(points) and all(passed(row) for row in rows) else 1


def main(request):
    host = request['host']
    assert host in ('host-94-26', 'host-94-18')
    assert subprocess.check_output(['hostname', '-s'], timeout=10).decode().strip() == host
    suffix = '-host18' if host == 'host-94-18' else ''
    root = SESSION_ROOT / f'run-{REVISION[:7]}{suffix}-a1'
    if request['action'] == 'build-entry-pipe':
        # Use the same production source and platform, with a separate build directory.
        with worker_container(root, 'build', 64) as cid:
            tools = root / 'entry-pipe-tools'
            tools.mkdir()
            for filename, content in request['build_files'].items():
                assert filename in ('build_profile.py', 'profile_capture.h')
                (tools / filename).write_text(content)
            command = ('set -e; source /opt/sim/cann-env.sh; '
                       'export PATH=/opt/sim/Ascend/cann-9.1.0/tools/bisheng_compiler/bin:'
                       '/opt/sim/Ascend/cann-9.1.0/aarch64-linux/ccec_compiler/bin:$PATH; '
                       'exec python3 /work/entry-pipe-tools/build_profile.py entry-pipe')
            with (root / 'profile-pipe-build.log').open('xb') as log:
                code = subprocess.run(['docker', 'exec', cid, 'bash', '-c', command],
                                      stdout=log, stderr=subprocess.STDOUT, timeout=340).returncode
        result = json_file(root / 'profile-pipe-build/build-summary.json')
        print(json.dumps(result, separators=(',', ':')))
        return code
    if request['action'] == 'recover-container':
        identity = json_file(root / 'identity.json')
        assert identity['revision'] == REVISION
        cid = identity['container_id']
        details = json.loads(subprocess.check_output(['docker', 'inspect', cid], timeout=20))[0]
        assert details['Image'] == IMAGE and details['Config']['Labels']['crosszone.session'] == SESSION
        assert not details['State']['Running']
        active = subprocess.check_output(['docker', 'ps', '--filter',
                  'label=crosszone.session=' + SESSION, '--format', '{{.ID}}'], timeout=15)
        assert not active.strip()
        subprocess.run(['docker', 'start', cid], capture_output=True, check=True, timeout=30)
        assert task_container(root, REVISION) == cid
        print(json.dumps({'host':host, 'container_restarted': True, 'old_exit': details['State']['ExitCode']}))
        return 0
    if request['action'] in ('build-inline-post', 'build-fixed-events', 'build-timing-only'):
        variant = request['action'][len('build-'):]
        build_dir = {'inline-post': 'profile-inline-build', 'fixed-events': 'profile-fixed-events-build',
                     'timing-only': 'profile-timing-build'}[variant]
        # Use the same production source and platform, with a separate build directory.
        with worker_container(root, 'build', 64) as cid:
            tools = root / (variant + '-tools')
            tools.mkdir()
            for filename, content in request['build_files'].items():
                assert filename in ('build_profile.py', 'profile_capture.h')
                (tools / filename).write_text(content)
            command = ('set -e; source /opt/sim/cann-env.sh; '
                       'export PATH=/opt/sim/Ascend/cann-9.1.0/tools/bisheng_compiler/bin:'
                       '/opt/sim/Ascend/cann-9.1.0/aarch64-linux/ccec_compiler/bin:$PATH; '
                       f'exec python3 /work/{variant}-tools/build_profile.py {variant}')
            with (root / (build_dir + '.log')).open('xb') as log:
                code = subprocess.run(['docker', 'exec', cid, 'bash', '-c', command],
                                      stdout=log, stderr=subprocess.STDOUT, timeout=340).returncode
        result = json_file(root / build_dir / 'build-summary.json')
        print(json.dumps(result, separators=(',', ':')))
        return code
    if request['action'] == 'recover-profile':
        case = root / 'cases' / request['case']
        assert case.parent == root / 'cases' and case.name == 'cores2-chunk64-a3'
        row = report_for(case)
        assert row['returncode'] == 0 and row['status'] == 'failed'
        assert 'Missing dump bytes:' in row['error'] and 'profile.bin' in row['error']
        with (root / 'stage.lock').open('a') as stage:
            fcntl.flock(stage, fcntl.LOCK_EX)
            tools, _ = stage_tools(root, request['files'])
        script = str(Path('/work') / tools.relative_to(root) / 'recover_profile.py')
        with worker_container(root, 'profile-recover', 64) as cid:
            log = root / 'hapi-logs' / (case.name + '.recover-profile.log')
            with log.open('xb') as stream:
                proc = subprocess.run(['docker', 'exec', cid, 'python3', script, case.name],
                                      stdout=stream, stderr=subprocess.STDOUT, timeout=295)
        result = report_for(case)
        errors = (root / 'hapi-logs' / (case.name + '.recover-profile.log')).read_text(errors='replace').splitlines()
        print(json.dumps({'rows': [result], 'diagnostic': errors[-1:] if proc.returncode else []}))
        return proc.returncode
    if request['action'] == 'diagnose-log':
        cases = request['cases']
        reports = []
        for label in cases:
            assert re.fullmatch(r'cores[1248]-chunk(16|32|64|128)-a[0-9]+', label)
            case = root / 'cases' / label
            path = case / 'sim.log'
            patterns = (
                r'Unknown low level mem[^\n]{0,120}',
                r'(?i:(?:overflow|exception|timeout|SIGSEGV|SIGTERM|assertion failed|fatal|out of memory|deadlock|invalid address))[^\n]{0,100}',
                r'(?:at |current cycle:? |Cycle:? )\d{6,}',
            )
            matches = []
            with path.open('rb') as log:
                total = log.seek(0, 2)
                log.seek(max(0, total - 2 * 1024 * 1024))
                tail = log.read().decode('utf-8', errors='replace')
            for pattern in patterns:
                matches.extend(dict.fromkeys(re.findall(pattern, tail)))
            updates = re.findall(r'\b[0-9]+\.[0-9]+\s*(?:us|ns)\b', tail)
            trace_paths = sorted((case / 'OUTPUT' / 'stat_trace').glob('Host*_ba_layer_trace.json'))
            trace_rows = []
            for trace in trace_paths:
                with trace.open('rb') as stream:
                    end = stream.seek(0, 2)
                    stream.seek(max(0, end - 256 * 1024))
                    suffix = stream.read()
                times = re.findall(rb'"ts"\s*:\s*(-?[0-9]+(?:\.[0-9]+)?)', suffix)
                if times:
                    trace_rows.append((trace.name, end, max(map(float, times))))
            record = json_file(case / 'phase-result.json')
            reports.append({'case': label, 'error': record.get('error'),
                            'returncode': record.get('returncode'), 'log_bytes':total,
                            'tail_events': updates[-5:],
                            'bus_trace_count': len(trace_paths),
                            'trace_frontier_top5': sorted(trace_rows, key=lambda row: row[2], reverse=True)[:5],
                            'log_matches': matches[-12:]})
        print(json.dumps({'diagnoses': reports}))
        return 0
    if request['action'] == 'diagnose-kill':
        label = request['label']
        assert label == 'cores1-chunk16-a1'
        case = root / 'cases' / label
        record = json_file(case / 'phase-result.json')
        assert record['candidate_commit'] == REVISION and record['returncode'] == -9
        cid = task_container(root, REVISION)
        started_ms = int(record['started_unix'])
        end_ms = int(started_ms + record['sst_wall_seconds']) + 30
        event_command = ['docker', 'events', '--since', str(started_ms), '--until', str(end_ms),
                         '--filter', 'container=' + cid, '--filter', 'event=oom', '--format', '{{.Action}}']
        event = subprocess.run(event_command, capture_output=True, timeout=20)
        memory_command = ['docker', 'exec', cid, 'bash', '-c',
                          'for path in /sys/fs/cgroup/memory.events /sys/fs/cgroup/memory/memory.max_usage_in_bytes '
                          '/sys/fs/cgroup/memory/memory.limit_in_bytes /sys/fs/cgroup/memory/memory.failcnt; '
                          'do if test -r "$path"; then basename "$path"; cat "$path"; fi; done']
        memory = subprocess.check_output(memory_command, timeout=20).decode('ascii').splitlines()
        at = subprocess.run(['dmesg', '--color=never'], capture_output=True, timeout=10)
        oom_matches = [line for line in at.stdout.decode(errors='replace').splitlines()
                       if cid[:12] in line and ('oom' in line.lower() or 'kill' in line.lower())]
        print(json.dumps({'case': label, 'returncode': record['returncode'],
                          'docker_oom_events': event.stdout.decode('ascii').splitlines(),
                          'cgroup_memory': memory, 'host_log_matches': len(oom_matches)}))
        return 0
    if request['action'] == 'resize':
        cid = task_container(root, REVISION)
        assert request['memory_gib'] == 128
        with (root / 'matrix.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = status(root)
            assert result['progress']['stage'] == 'failed'
            row = next(row for row in result['rows'] if row['case'] == 'cores1-chunk16-a1')
            assert row['final'] and row['returncode'] == -9
            subprocess.run(['docker', 'update', '--memory=128g', '--memory-swap=128g', cid],
                           check=True, capture_output=True, timeout=25)
            command = ['docker', 'inspect', '--format', '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}', cid]
            sizes = subprocess.check_output(command, timeout=15).decode('ascii').split()
            assert [int(value) for value in sizes] == [128 * 2**30] * 2
        print(json.dumps({'resized_gib':128,'host':host}))
        return 0
    if request['action'] == 'finish':
        cid = task_container(root, REVISION)
        result = status(root)
        rows = { (row.get('cores'), row.get('chunk_tokens')) for row in result['rows'] if passed(row) }
        assert rows == set(POINTS)
        command = ['docker', 'ps', '--filter', 'label=crosszone.session=' + SESSION, '--format', '{{.ID}}']
        active = subprocess.check_output(command, timeout=20).decode('ascii').splitlines()
        assert len(active) == 1 and cid.startswith(active[0])
        with (root / 'matrix.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            subprocess.run(['docker', 'stop', '--time', '20', cid], check=True, capture_output=True, timeout=50)
        report = status(root)
        assert report['docker_state'] == ['false', 'false']
        print(json.dumps({'matrix_passed':len(rows),'docker_state':report['docker_state']}))
        return 0
    if request['action'] == 'status':
        report = status(root)
        command = ['docker', 'ps', '--filter', 'label=crosszone.session=' + SESSION, '--format', '{{.ID}} {{.Names}}']
        active = subprocess.check_output(command, timeout=15).decode('ascii').splitlines()
        runs = {}
        for line in active:
            cid, name = line.split()
            inspect = '''import json, re
from pathlib import Path
count = 0
cases = set()
for entry in Path('/proc').glob('[0-9]*/cmdline'):
    try:
        arguments = entry.read_bytes().split(bytes([0]))
    except (OSError, ProcessLookupError):
        continue
    if not any(arg.endswith((b'/sim_case.py', b'/sstsim.x')) for arg in arguments):
        continue
    count += 1
    cases.update(match.decode('ascii') for arg in arguments for match in re.findall(
        rb'cores[1248]-chunk(?:16|32|64|128)-a[0-9]+', arg))
memory = Path('/sys/fs/cgroup/memory/memory.max_usage_in_bytes')
print(json.dumps({'known': True, 'processes': count, 'cases': sorted(cases),
                  'memory_peak_bytes': int(memory.read_text()) if memory.is_file() else None}))
'''
            proc = subprocess.run(['docker', 'exec', cid, 'python3', '-c', inspect],
                                  timeout=10, capture_output=True)
            runs[name] = json.loads(proc.stdout) if proc.returncode == 0 else {'known': False}
        report['active'] = runs
        report.pop('identity', None)
        report.pop('bootstrap-result', None)
        observed = set(request.get('known_cases', []))
        report['rows'] = [row for row in report['rows'] if row['case'] not in observed or not row['final']]
        for row in report['rows']:
            if not row['final']:
                for field in tuple(row):
                    if field not in ('case', 'status', 'final'):
                        del row[field]
        print(json.dumps(report, separators=(',', ':')))
        return 0
    assert request['action'] == 'run'
    return run(root, task_container(root, REVISION), request)
