"""Build the pinned BS32 relay benchmark and collect results from each exclusive server."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile

from build import SSH, archive, git, pin


HOSTS = ('90.90.93.81', '90.90.93.83')
TILEXR = 'https://github.com/LingquLab/TileXR.git'
REVISION = '84da56ab1ada6f7950f95944ec678da36f41bca1'
RUNTIME = '0797410beab1da40800611affc9c512cd42525ba'
GREEN_BASE = Path(r'D:\ChenBohao\09-Tackle\01-BUX-disp-comb\result-trace')
ROOT = '/root/ubx-relay-bs32-' + REVISION[:7] + '-exclusive'
GREEN = GREEN_BASE / ('relay-bs32-' + REVISION[:7])
OPTIONS = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10']


def prepare(work):
    repo = work / 'tilexr'
    pin(repo, TILEXR, (REVISION, RUNTIME))
    components = [archive(work, repo, 'source', REVISION, 'dispatch/dispatch-release'),
                  archive(work, repo, 'runtime', RUNTIME)]
    for name, origin in [('spdlog', 'https://github.com/gabime/spdlog.git'),
                         ('picojson', 'https://github.com/kazuho/picojson.git')]:
        path = '3rdparty/' + name
        mode, kind, revision, recorded_path = git(repo, 'ls-tree', RUNTIME, path).split()
        assert (mode, kind, recorded_path) == ('160000', 'commit', path)
        dependency = work / name
        pin(dependency, origin, (revision,))
        components.append(archive(work, dependency, name, revision))
    bundle = work / 'sources.tar'
    with tarfile.open(bundle, 'w') as package:
        for component in components:
            package.add(component, arcname=component.name, recursive=False)
    return bundle, hashlib.sha256(bundle.read_bytes()).hexdigest()


def command(executable, args, timeout, payload=None):
    return subprocess.run([str(executable), *OPTIONS, *args], input=payload,
                          capture_output=True, timeout=timeout)


def ssh(host, remote, timeout=25, payload=None):
    return command(SSH, ['root@' + host, remote], timeout, payload)


def pull(host, relative, path):
    result = command(SSH.with_name('scp.exe'), [f'root@{host}:{ROOT}/{relative}', str(path)], 45)
    return result.returncode == 0


def observe(host):
    result = ssh(host, f'cat {ROOT}/status.txt')
    status = result.stdout.decode().strip() if result.returncode == 0 else 'not-started'
    data = {'host': host, 'phase': status}
    for filename, key in [('occupancy.json', 'occupancy'), ('toolkit.txt', 'toolkit')]:
        result = ssh(host, f'cat {ROOT}/{filename}')
        if result.returncode == 0:
            value = result.stdout.decode().strip()
            data[key] = json.loads(value) if filename.endswith('.json') else value
    return data


def run(host, bundle, digest, comparison):
    destination = GREEN / host
    destination.mkdir()
    ssh_output = destination / 'execution.log'
    step = 'create-directory'
    try:
        assert ssh(host, f'mkdir {ROOT}').returncode == 0, step
        step = 'upload-sources'
        uploaded = command(SSH.with_name('scp.exe'), [str(bundle), f'root@{host}:{ROOT}/sources.tar'], 240)
        assert uploaded.returncode == 0, step
        step = 'build-and-run'
        script = Path(__file__).with_name('relay_bs32_remote.sh').read_bytes().replace(b'\r\n', b'\n')
        cmd = shlex.join(['bash', '-s', '--', ROOT, digest, comparison])
        executed = ssh(host, f'{cmd} > {ROOT}/execution.log 2>&1', 1360, script)
        step = 'collect-results'
        data = observe(host)
        data['exit_code'] = executed.returncode
        if executed.returncode != 0:
            pull(host, 'execution.log', ssh_output)
            return data
        for name in ('summary.json', 'last-round.json', 'slowest-rank-rounds.json'):
            assert pull(host, 'run/' + name, destination / name), name
        assert pull(host, 'artifacts.sha256', destination / 'artifacts.sha256')
        data['results'] = json.loads((destination / 'summary.json').read_text())
        data['local_path'] = str(destination)
        return data
    except (AssertionError, OSError, subprocess.TimeoutExpired, ValueError) as error:
        return {'host': host, 'phase': step, 'error': type(error).__name__}


def print_report(report, comparison):
    if comparison == 'pairings' and all('results' in host for host in report.get('hosts', [])) and report.get('hosts'):
        print(json.dumps({
            host['host'].rsplit('.', 1)[1]: [[round(c['median_us'], 3), round(c['max_us'], 3),
                *(round(pair['median_us'], 3) for pair in c['pairs'])] for c in host['results']['cases']]
            for host in report['hosts']
        }, separators=(',', ':')))
    else:
        print(json.dumps(report, separators=(',', ':')))


def main():
    global REVISION, ROOT, GREEN
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path)
    options = parser.parse_args()
    args = json.loads(options.args_file.read_text(encoding='utf-8')) if options.args_file else {}
    REVISION = args.get('revision', REVISION)
    comparison = args.get('comparison', 'default')
    attempt = args.get('attempt', 1)
    assert re.fullmatch('[0-9a-f]{40}', REVISION) and comparison in ('default', 'pairings')
    assert isinstance(attempt, int) and attempt >= 1
    label = 'relay-bs32-' + ('pairings-' if comparison == 'pairings' else '') + REVISION[:7]
    if attempt > 1:
        label += f'-retry{attempt}'
    ROOT = '/root/ubx-' + label + '-exclusive'
    GREEN = GREEN_BASE / label
    if args.get('status'):
        receipt = GREEN / 'receipt.json'
        if receipt.is_file():
            print_report(json.loads(receipt.read_text()), comparison)
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                print(json.dumps({'hosts': list(pool.map(observe, HOSTS))}))
        return True
    GREEN.mkdir(parents=True, exist_ok=False)
    try:
        with tempfile.TemporaryDirectory(prefix='ubx-relay-bs32-', dir=Path.cwd()) as directory:
            bundle, digest = prepare(Path(directory).resolve())
            with ThreadPoolExecutor(max_workers=2) as pool:
                hosts = list(pool.map(lambda host: run(host, bundle, digest, comparison), HOSTS))
        report = {'revision': REVISION, 'runtime_revision': RUNTIME, 'source_sha256': digest, 'hosts': hosts}
    except (AssertionError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        report = {'revision': REVISION, 'preparation_error': str(error)}
    (GREEN / 'receipt.json').write_text(json.dumps(report))
    print_report(report, comparison)
    return all('results' in host for host in report.get('hosts', [])) and 'hosts' in report


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
