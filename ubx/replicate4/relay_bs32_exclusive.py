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


def run(host, bundle, digest, comparison, resume=False, idle_wait=120):
    destination = GREEN / host
    destination.mkdir(exist_ok=resume)
    log = 'resume.log' if resume else 'execution.log'
    step = 'verify-resume' if resume else 'create-directory'
    try:
        if resume:
            assert observe(host)['phase'] == 'occupancy 1'
        else:
            assert ssh(host, f'mkdir {ROOT}').returncode == 0, step
            step = 'upload-sources'
            uploaded = command(SSH.with_name('scp.exe'), [str(bundle), f'root@{host}:{ROOT}/sources.tar'], 240)
            assert uploaded.returncode == 0, step
        step = 'build-and-run'
        script = Path(__file__).with_name('relay_bs32_remote.sh').read_bytes().replace(b'\r\n', b'\n')
        cmd = shlex.join(['bash', '-s', '--', ROOT, digest, comparison, 'resume' if resume else 'build',
                          str(idle_wait)])
        executed = ssh(host, f'{cmd} > {ROOT}/{log} 2>&1', 1360, script)
        step = 'collect-results'
        data = observe(host)
        data['exit_code'] = executed.returncode
        if executed.returncode != 0:
            pull(host, log, destination / log)
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
    if comparison == 'staged':
        summary = {}
        for host in report.get('hosts', []):
            key = host['host'].rsplit('.', 1)[1]
            if 'results' not in host:
                summary[key] = host
                continue
            summary[key] = []
            for case in host['results']['cases']:
                stats = [case['median_us'], case['max_us'], case['core_time']['median_us'],
                         case['rank_time']['median_us'], case['rank_minus_slowest_core']['median_us'],
                         case['rank_minus_slowest_core']['max_us']]
                if 'barrier_wait' in case:
                    stats.extend(case['barrier_wait'][field] for field in ('median_us', 'p95_us', 'max_us'))
                summary[key].append([round(value, 2) for value in stats])
        print(json.dumps(summary or report, separators=(',', ':')))
        return
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
    assert re.fullmatch('[0-9a-f]{40}', REVISION) and comparison in ('default', 'pairings', 'staged')
    assert isinstance(attempt, int) and attempt >= 1
    label = 'relay-bs32-' + (comparison + '-' if comparison != 'default' else '') + REVISION[:7]
    if attempt > 1:
        label += f'-retry{attempt}'
    ROOT = '/root/ubx-' + label + '-exclusive'
    GREEN = GREEN_BASE / label
    receipt = GREEN / 'receipt.json'
    if args.get('status'):
        if receipt.is_file():
            print_report(json.loads(receipt.read_text()), comparison)
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                print(json.dumps({'hosts': list(pool.map(observe, HOSTS))}))
        return True
    resume_host = args.get('resume_host')
    if resume_host:
        assert resume_host in HOSTS
        idle_wait = args.get('idle_wait', 120)
        assert type(idle_wait) is int and 0 <= idle_wait <= 600
        report = json.loads(receipt.read_text())
        assert report['revision'] == REVISION
        current, = (h for h in report['hosts'] if h['host'] == resume_host)
        assert current['phase'] == 'occupancy 1'
        recovered = run(resume_host, None, report['source_sha256'], comparison, resume=True, idle_wait=idle_wait)
        report['hosts'] = [recovered if h['host'] == resume_host else h for h in report['hosts']]
        receipt.write_text(json.dumps(report))
        print_report(report, comparison)
        return 'results' in recovered
    GREEN.mkdir(parents=True, exist_ok=False)
    try:
        with tempfile.TemporaryDirectory(prefix='ubx-relay-bs32-', dir=Path.cwd()) as directory:
            bundle, digest = prepare(Path(directory).resolve())
            with ThreadPoolExecutor(max_workers=2) as pool:
                hosts = list(pool.map(lambda host: run(host, bundle, digest, comparison), HOSTS))
        report = {'revision': REVISION, 'runtime_revision': RUNTIME, 'source_sha256': digest, 'hosts': hosts}
    except (AssertionError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        report = {'revision': REVISION, 'preparation_error': str(error)}
    receipt.write_text(json.dumps(report))
    print_report(report, comparison)
    return all('results' in host for host in report.get('hosts', [])) and 'hosts' in report


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
