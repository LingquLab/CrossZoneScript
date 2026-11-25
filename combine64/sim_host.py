# Copyright (c) 2026 TileXR Project.
"""Check the task's container and launch one foreground SST case."""
import fcntl
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import traceback

SESSION = 'de038086-2b21-40e2-bad9-65360186ad97'
REVISION = 'b5383cfd65828a165fae9957d287967136c06260'
ROOT = Path(f'/mnt/share/tilexr-sim-jobs/{SESSION}/run-b5383cf-a1')
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


def task_container():
    identity = json_file(ROOT / 'identity.json')
    assert identity['revision'] == REVISION and identity['image'] == IMAGE
    cid = identity['container_id']
    info = json.loads(subprocess.check_output(['docker', 'inspect', cid], timeout=20))[0]
    assert info['Id'] == cid and info['Image'] == IMAGE
    assert info['HostConfig']['NetworkMode'] == 'none'
    assert info['Config']['Labels']['crosszone.session'] == SESSION
    assert info['State']['Running']
    mounts = {m['Destination']: m for m in info['Mounts']}
    assert mounts['/work']['Source'] == str(ROOT)
    assert mounts['/work/source']['Source'] == str(ROOT / 'source')
    assert mounts['/work/source']['RW'] is False
    return cid


def stage_tools(files):
    key = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]
    tools = ROOT / 'tools' / key
    tools.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        assert name in {'fixture.py', 'prefill.cpp', 'sim_case.py', 'return_case.py', 'addressed_exports.py'}
        path = tools / name
        if path.exists():
            assert path.read_bytes() == content.encode()
        else:
            path.write_bytes(content.encode())
    platform = ROOT / 'platform'
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
    return tools, platform, selected


def main(args):
    action, tokens, attempt, timeout = (args[key] for key in ('action', 'tokens', 'attempt', 'timeout'))
    case = ROOT / 'cases' / f't{tokens}-{action}-a{attempt}'
    if action == 'status':
        report = {'root': str(ROOT), 'exists': ROOT.is_dir()}
        for name in ('progress.json', 'build-summary.json', 'bootstrap-result.json'):
            path = ROOT / name
            if path.is_file():
                report[path.stem] = json_file(path)
        print(json.dumps(report, separators=(',', ':')))
        return 0
    try:
        cid = task_container()
        logs = ROOT / 'hapi-logs'
        logs.mkdir(exist_ok=True)
        log_path = logs / (case.name + '.log')
        with (ROOT / 'sst.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            tools, platform, platform_hashes = stage_tools(args['files'])
            config = {
                'action': action, 'tokens': tokens, 'attempt': attempt, 'timeout': timeout,
                'platform_sha256': platform_hashes, 'source_case': args.get('source_case'),
            }
            container_tools = str(Path('/work') / tools.relative_to(ROOT))
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
            report = {'status': 'preparation_or_runner_failed', 'errors': errors[:5]}
        report.update(case=str(case), host_log=str(log_path), container_exit=proc.returncode)
        qualified = report.get('status') == 'sst_and_checker_passed' and proc.returncode == 0
        print(json.dumps(report, separators=(',', ':')))
        return 0 if qualified else 1
    except Exception as error:
        if ROOT.is_dir():
            log = ROOT / f'{case.name}-host-error.log'
            log.write_text(traceback.format_exc())
        report = {'status': 'host_setup_failed', 'error_class': type(error).__name__,
                  'detail': str(error)[:240], 'case': str(case)}
        print(json.dumps(report, separators=(',', ':')))
        return 1
