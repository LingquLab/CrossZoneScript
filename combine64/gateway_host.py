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

SESSION = '5c53b5f4-6259-4c0d-8a3b-cde264d9cdcb'
REVISION = '4d37ae67cf2f6baa06b3efbe65c71649a6f24203'
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


def stage_tools(root, files):
    key = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]
    tools = root / 'tools' / key
    tools.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        assert name in {'gateway_fixture.py', 'gateway_args.cpp', 'gateway_case.py', 'gateway_profile.py',
                        'addressed_exports.py', 'clock_evidence.json'}
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


ROOT = SESSION_ROOT / f'run-{REVISION[:7]}-host18-a1'


def logs(case, label):
    result = case / 'phase-result.json'
    run = case / 'run.json'
    row = json_file(result if result.is_file() else run) if result.is_file() or run.is_file() else {'status': 'pending'}
    row['case'] = label
    for key in ('command', 'checked_bytes', 'pid'):
        row.pop(key, None)
    if row['status'] not in ('running', 'pending', 'sst_and_checker_passed'):
        sim = case / 'sim.log'
        if sim.is_file():
            with sim.open('rb') as f:
                f.seek(max(0, f.seek(0, os.SEEK_END) - 262144))
                tail = f.read().decode(errors='replace')
            row['sst_error'] = [line.strip()[:240] for line in tail.splitlines()
                                if re.search(r'error|fatal|unknown low level|signal', line, re.I)][-6:]
        trace = ROOT / 'hapi-logs' / (label + '.log')
        if trace.is_file():
            row['runner_error'] = trace.read_text(errors='replace').splitlines()[-6:]
    return row


def status_summary(row):
    fields = ('case', 'status', 'cores', 'chunk_tokens', 'started_unix', 'sst_wall_seconds',
              'runner_wall_seconds', 'platform_simulated_us', 'profiling_operator_us',
              'profiling_lanes', 'profiling_dropped_records', 'checked_ranks', 'guards',
              'chunk_sample_us', 'mean_chunk_us', 'trace_sha256', 'error', 'sst_error', 'runner_error')
    return {key: row[key] for key in fields if key in row}


def run_case(point, tools, platform, attempt, timeout):
    cores, chunk = point
    micro = cores == 1
    label = f'cores{cores}-chunk{chunk}-a{attempt}'
    existing = sorted((ROOT / 'cases').glob(f'cores{cores}-chunk{chunk}-a*'))
    for prior in existing:
        row = logs(prior, prior.name)
        if row['status'] == 'sst_and_checker_passed':
            return row
    directory = ROOT / 'hapi-logs'
    directory.mkdir(exist_ok=True)
    case = ROOT / 'cases' / label
    with (directory / (label + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not case.exists(), 'use a new attempt for incomplete cases'
        config = dict(revision=REVISION, cores=cores, chunk=chunk, micro=micro, label=label,
                      platform_sha256=platform, timeout=timeout, threads=8)
        script = '/work/' + str(tools.relative_to(ROOT) / 'gateway_case.py')
        with worker_container(ROOT, f'{cores}-{chunk}-a{attempt}', 128) as cid:
            with (directory / (label + '.log')).open('xb') as output:
                code = subprocess.run(['docker', 'exec', cid, 'python3', script, json.dumps(config)],
                                      stdout=output, stderr=subprocess.STDOUT, timeout=timeout + 420).returncode
        row = logs(case, label)
        row['case_process_exit'] = code
        return row


def main(request):
    assert subprocess.check_output(['hostname', '-s'], timeout=10).decode().strip() == 'host-94-18'
    identity = json_file(ROOT / 'identity.json')
    assert identity['revision'] == REVISION and identity['image'] == IMAGE
    action = request['action']
    if action == 'status':
        active = subprocess.check_output(['docker', 'ps', '--filter', 'label=crosszone.session=' + SESSION,
                                          '--format', '{{.Names}}'], timeout=20).decode().splitlines()
        rows = [logs(case, case.name) for case in sorted((ROOT / 'cases').glob('cores*-chunk*-a*'))]
        state = dict(active=active, rows=[status_summary(row) for row in rows])
        if request.get('show_legacy_coordinator'):
            # Match the exact outer command of the preceding 15-point matrix dispatch.
            processes = subprocess.check_output(['ps', '-eo', 'args='], timeout=15).decode().splitlines()
            state['legacy_coordinator_running'] = any(command.strip() == 'timeout --kill-after=25 34280 python3 -'
                                                       for command in processes)
        print(json.dumps(state, separators=(',', ':')))
        return 0
    if action == 'diagnose-micro':
        evidence = {}
        for chunk in (64, 128, 512):
            case = ROOT / 'cases' / f'cores1-chunk{chunk}-a1'
            logfile = case / 'sim.log'
            with logfile.open('rb') as stream:
                size = stream.seek(0, os.SEEK_END)
                stream.seek(max(0, size - 4096))
                tail = stream.read().decode(errors='replace').splitlines()
            evidence[chunk] = dict(log_bytes=size, tail=[line[:180] for line in tail[-9:]],
                                   output_dump_count=len(list((case / 'bin').glob('*.bin'))))
        patterns = r'num_chips|num_ranks|range\(|world_size|stop.at|cycle_limit|[Ss]etProgramOption'
        for name in ('test_UBusSim.py', 'v310_config/davidV100_simulator_config.yml'):
            lines = (ROOT / 'platform' / name).read_text().splitlines()
            evidence[name] = [f'{index}: {line.strip()[:200]}' for index, line in enumerate(lines, 1)
                              if re.search(patterns, line, re.I)][:20]
        print(json.dumps(evidence))
        return 0
    if action == 'finish':
        active = subprocess.check_output(['docker', 'ps', '--filter', 'label=crosszone.session=' + SESSION,
                                          '--format', '{{.ID}}'], timeout=20).decode().splitlines()
        info = json.loads(subprocess.check_output(['docker', 'inspect', identity['container_id']], timeout=20))[0]
        assert info['Id'] == identity['container_id'] and info['Image'] == IMAGE
        assert info['Config']['Labels']['crosszone.session'] == SESSION
        assert all(info['Id'].startswith(container) for container in active), active
        if info['State']['Running']:
            subprocess.run(['docker', 'stop', '--time', '10', info['Id']], capture_output=True, check=True, timeout=25)
        print(json.dumps({'host': '18', 'owned_containers_running': 0}))
        return 0
    assert action == 'run'
    points = [tuple(value) for value in request['points']]
    allowed = {(cores, chunk) for cores in (8, 16, 24, 32) for chunk in (16, 32, 64, 128)}
    allowed |= {(1, chunk) for chunk in (16, 32, 64, 128, 512)}
    assert points and len(points) == len(set(points)) and set(points) <= allowed
    attempt = request.get('attempt', 1)
    timeout = request.get('timeout', 1800)
    assert type(attempt) is int and 1 <= attempt <= 20
    assert type(timeout) is int and 200 <= timeout <= 3600
    desired = request.get('workers', 1)
    assert type(desired) is int and 1 <= desired <= 4
    available = next(int(line.split()[1]) // 1024**2 for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    workers = max(1, min(desired, len(points), len(os.sched_getaffinity(0)) // 8, (available - 128) // 128))
    with (ROOT / 'stage.lock').open('a') as stage:
        fcntl.flock(stage, fcntl.LOCK_EX)
        tools, platform = stage_tools(ROOT, request['files'])
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run_case, point, tools, platform, attempt, timeout) for point in points]
        rows = []
        for point, future in zip(points, futures):
            try:
                rows.append(future.result())
            except Exception as error:
                rows.append(dict(cores=point[0], chunk_tokens=point[1], status='orchestration_failed',
                                 error=f'{type(error).__name__}: {error}'[:240]))
    print(json.dumps(dict(workers=workers, available_gib=available, rows=rows), separators=(',', ':')))
    return 0 if all(row['status'] == 'sst_and_checker_passed' for row in rows) else 1
