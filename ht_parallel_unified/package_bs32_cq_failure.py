"""Copy the retained BS32 CQ failure cases and logs to the green workstation."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import zipfile

from config import T32_CASE, T32_CROSS_STRIPE_CASE, T32_LONG_CASE

here = Path(__file__).resolve().parent
sys.path[:0] = [str(here.parent / 'ht_t32_e2e'), str(here.parent / 'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST

cases = (T32_CASE, T32_LONG_CASE, T32_CROSS_STRIPE_CASE)
root = '/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native'
archive_name = 'ht-bs32-cross-return-cq-failure-cases.zip'
options = ['-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
           '-o', 'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1',
           '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no']

source = f'cases = {cases!r}\nroot = {root!r}\n' + r'''
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile
import zipfile


def write_case(archive, case):
    run = json.loads((case / 'run.json').read_text())
    if run['status'] != 'timed_out':
        raise ValueError('expected a retained timed-out run')
    manifest = json.loads((case / 'case-manifest.json').read_text())
    members = set(manifest['files'])
    members.update(('case-manifest.json', 'run.json', 'sim.log', 'native-runner.log'))
    for name in ('phase-summary.json', 'unified-result.json', 'check.log'):
        if (case / name).is_file():
            members.add(name)
    counts = {}
    for layer in ('ba', 'dl'):
        paths = list((case / 'OUTPUT/stat_trace').glob(f'Host*_{layer}_layer_trace.json'))
        if len(paths) != 64:
            raise ValueError(f'incomplete {layer} trace inventory')
        counts[layer] = len(paths)
        members.update(path.relative_to(case).as_posix() for path in paths)
    for name in sorted(members):
        rel = PurePosixPath(name)
        path = case / rel
        if rel.is_absolute() or '..' in rel.parts or not path.is_file() or not path.resolve().is_relative_to(case):
            raise ValueError('invalid case file')
        archive.write(path, f'{case.name}/{rel}')
    return dict(case=case.name, files=len(members), trace_counts=counts)


output = Path(tempfile.mkdtemp(prefix='ht-bs32-cq-bundle-', dir='/tmp')) / 'bundle.zip'
try:
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        results = [write_case(archive, (Path(root) / case).resolve()) for case in cases]
    with output.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    print(json.dumps(dict(remote_path=str(output), sha256=digest.hexdigest(),
                          bytes=output.stat().st_size, cases=results), separators=(',', ':')))
except Exception:
    output.unlink(missing_ok=True)
    output.parent.rmdir()
    raise
'''


def sha256(path):
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def check_archive(path, report):
    if path.stat().st_size != report['bytes'] or sha256(path) != report['sha256']:
        raise ValueError('archive checksum differs after transfer')
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        for case in cases:
            files = {name.removeprefix(f'{case}/') for name in names if name.startswith(f'{case}/')}
            required = {'case-manifest.json', 'sim.log', 'native-runner.log', 'run.json',
                        'workload.yml', 'kernel/inter_server_send.o', 'fixture-data/manifest.json'}
            if not required.issubset(files) or not any(name.startswith('input_bin/') for name in files):
                raise ValueError('incomplete case archive')
            for layer in ('ba', 'dl'):
                count = sum(name.startswith('OUTPUT/stat_trace/') and name.endswith(f'_{layer}_layer_trace.json')
                            for name in files)
                if count != 64:
                    raise ValueError('incomplete trace archive')


def main():
    compile(source, '<bs32-cq-bundle>', 'exec')
    destination = Path('D:/project/tilexr-inter-server-sst') / archive_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    with askpass_environment() as env:
        remote = subprocess.run(['ssh', '-T', *options, HOST, 'python3', '-'],
                                input=source.encode(), env=env, capture_output=True, timeout=360)
        if remote.returncode:
            raise RuntimeError(f'server packaging exited {remote.returncode}')
        report = json.loads(remote.stdout)
        remote_path = report.pop('remote_path')
        if not re.fullmatch(r'/tmp/ht-bs32-cq-bundle-[a-z0-9_]+/bundle\.zip', remote_path):
            raise ValueError('unexpected temporary archive path')
        try:
            with tempfile.TemporaryDirectory(prefix='ht-bs32-cq-', dir=destination.parent) as temporary:
                partial = Path(temporary) / archive_name
                copy = subprocess.run(['scp', '-q', *options, f'{HOST}:{remote_path}', str(partial)],
                                      env=env, capture_output=True, timeout=240)
                if copy.returncode:
                    raise RuntimeError(f'green workstation transfer exited {copy.returncode}')
                check_archive(partial, report)
                if destination.exists():
                    destination = destination.with_name(f'{destination.stem}-{report["sha256"][:12]}.zip')
                if destination.exists():
                    check_archive(destination, report)
                else:
                    partial.rename(destination)
        finally:
            cleanup_source = (
                f'from pathlib import Path\np = Path({remote_path!r})\n'
                'p.unlink(missing_ok=True)\np.parent.rmdir()\n'
            )
            cleanup = subprocess.run(['ssh', '-T', *options, HOST, 'python3', '-'],
                                     input=cleanup_source.encode(), env=env, capture_output=True, timeout=25)
            report['server_temp_removed'] = cleanup.returncode == 0
    report['path'] = str(destination)
    print(json.dumps(report, separators=(',', ':')))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(json.dumps(dict(status='failed', error_type=type(error).__name__)))
        raise SystemExit(1)
