"""Download the verified per-case Perfetto traces to the requested green drive."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile

from bootstrap_green import REVISIONS, SESSION
from transport import connect

REVISION = REVISIONS['a50893f']
DESTINATION = Path(r'D:\00_项目\02_moonep_URAM\test 1128 机内发送')
FILES = ('phase-result.json', 'profiling-summary.json', 'profiling-records.json',
         'profiling/intra_send_perfetto.json', 'profiling/intra_send_batches.csv')
REMOTE = '''
import hashlib
import json
import socket
import sys
import tarfile
from pathlib import Path

assert socket.gethostname().split('.')[0] == 'host-94-' + HOST
suffix = '-host18' if HOST == '18' else ''
root = Path('/mnt/share/tilexr-sim-jobs') / SESSION / f'run-{REVISION[:7]}{suffix}-a1'
case = root / 'cases' / CASE
row = json.loads((case / 'phase-result.json').read_text())
assert row['status'] == 'sst_and_checker_passed' and row['candidate_commit'] == REVISION
assert row['checked_ranks'] == 64 and row['profiling_dropped_records'] == 0
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz', compresslevel=1) as archive:
    for name in FILES:
        path = case / name
        assert path.is_file() and not path.is_symlink()
        archive.add(path, arcname=name, recursive=False)
evidence = {}
for name in FILES:
    path = case / name
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    evidence[name] = dict(sha256=digest.hexdigest(), bytes=path.stat().st_size)
print(json.dumps(evidence), file=sys.stderr)
'''


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def verify(directory, cores, chunk, evidence):
    result = json.loads((directory / 'phase-result.json').read_text())
    assert result['candidate_commit'] == REVISION and result['status'] == 'sst_and_checker_passed'
    assert (result['cores'], result['chunk_tokens'], result['checked_ranks']) == (cores, chunk, 64)
    assert result['port0_die_histogram'] == {'0': 32, '1': 32}
    assert result['profiling_lanes'] == 64 * cores and result['profiling_dropped_records'] == 0
    assert set(evidence) == set(FILES)
    for name, item in evidence.items():
        path = directory / name
        assert path.stat().st_size == item['bytes'] > 0 and sha256(path) == item['sha256']
    capture = json.loads((directory / 'profiling/intra_send_perfetto.json').read_text())
    assert any(row['name'] == 'Doorbell' for row in capture['traceEvents'])


def download(client, host, case, cores, chunk):
    destination = DESTINATION / case
    if destination.exists():
        evidence = json.loads((destination / 'manifest.json').read_text())
        verify(destination, cores, chunk, evidence)
        return evidence
    with tempfile.TemporaryDirectory(dir=DESTINATION, prefix='.' + case + '-') as folder:
        temporary = Path(folder)
        zipped = temporary / 'trace.tar.gz'
        preamble = f'HOST={host!r}\nSESSION={SESSION!r}\nREVISION={REVISION!r}\nCASE={case!r}\nFILES={FILES!r}\n'
        stdin, stdout, stderr = client.exec_command('timeout --kill-after=15 480 python3 -', timeout=500)
        with ThreadPoolExecutor(max_workers=1) as executor:
            diagnostic = executor.submit(stderr.read, 32768)
            stdin.write(preamble + REMOTE)
            stdin.channel.shutdown_write()
            with zipped.open('wb') as stream:
                shutil.copyfileobj(stdout, stream, length=1024 * 1024)
            output = diagnostic.result()
        assert stdout.channel.recv_exit_status() == 0, 'remote_trace_archive_failed'
        evidence = json.loads(output)
        content = temporary / 'checked'
        with tarfile.open(zipped, 'r:gz') as archive:
            assert set(archive.getnames()) == set(FILES) and len(archive.getmembers()) == len(FILES)
            for name in FILES:
                item = archive.getmember(name)
                assert item.isfile()
                path = content / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as source, path.open('xb') as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
        verify(content, cores, chunk, evidence)
        (content / 'manifest.json').write_text(json.dumps(evidence, indent=2) + '\n')
        os.rename(content, destination)
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    host = args['host']
    assert host in ('26', '18')
    assert DESTINATION.anchor and Path(DESTINATION.anchor).is_dir()
    DESTINATION.mkdir(parents=True, exist_ok=True)
    report = {}
    with connect(host, args['auth']) as client:
        for cores, chunk, attempt in args['cases']:
            assert cores in (1, 2, 4, 8) and chunk in (16, 32, 64, 128)
            assert type(attempt) is int and 1 <= attempt <= 20
            case = f'cores{cores}-chunk{chunk}-a{attempt}'
            evidence = download(client, host, case, cores, chunk)
            report[case] = evidence['profiling/intra_send_perfetto.json']
    print(json.dumps({'directory': str(DESTINATION), 'cases': report}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'error_class': type(error).__name__}))
        raise SystemExit(1)
