"""Download verified SST trace archives from host-94-18 to green Windows."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
import shutil
import tarfile
import tempfile

from bootstrap_green import REVISIONS, SESSION
from target18 import CANDIDATE, connect

DESTINATION = Path(r'D:\00_项目\02_moonep_URAM\test1128 jetty数可配')
SIZES = (4, 32, 128)
POINTS = ('local-8x1', 'local-4x2', 'inter-16x8', 'inter-16x16', 'joint-16x8', 'joint-16x16')
REMOTE = r"""
import gzip
import json
from pathlib import Path
import socket
import sys
import tarfile

assert socket.gethostname().split('.')[0] == 'host-94-18'
root = Path('/mnt/share/tilexr-sim-jobs') / SESSION / 'run-c030900-host18-a1'
rows = json.loads((root / 'matrix-results.json').read_text())
row = rows[LABEL]
case = Path(row['case'])
assert case.resolve().parent == (root / 'cases').resolve()
result = json.loads((case / 'phase-result.json').read_text())
for report in (result, row):
    assert report['status'] == 'sst_and_checker_passed'
    assert report['candidate_commit'] == REVISION and report['checked_ranks'] == 64
    assert report['tokens'] == int(LABEL.split('-')[0])
    assert report['profiling_dropped_records'] == 0
assert result['attempt'] == row['attempt'] and result['stage'] == row['stage']

entries = [case / name for name in ('phase-result.json', 'profiling-records.json', 'profiling-summary.json')]
assert all(path.is_file() for path in entries)
directory = case / 'OUTPUT' / 'stat_trace'
traces = [path for path in sorted(directory.rglob('*'))
          if path.is_file() and not path.is_symlink() and directory.resolve() in path.resolve().parents]
entries.extend(traces)
with gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', compresslevel=1) as compressed:
    with tarfile.open(fileobj=compressed, mode='w|') as archive:
        for path in entries:
            archive.add(path, arcname=path.relative_to(case).as_posix(), recursive=False)
print(json.dumps({'sst_files': len(traces), 'sst_bytes': sum(path.stat().st_size for path in traces)}),
      file=sys.stderr)
"""


def verify_archive(path, label):
    with tarfile.open(path, 'r:gz') as archive:
        result = json.load(archive.extractfile('phase-result.json'))
        assert result['candidate_commit'] == REVISIONS[CANDIDATE]
        assert result['status'] == 'sst_and_checker_passed' and result['checked_ranks'] == 64
        assert result['tokens'] == int(label.split('-')[0])
        assert result['profiling_dropped_records'] == 0
        traces = [item for item in archive.getmembers()
                  if item.isfile() and item.name.startswith('OUTPUT/stat_trace/')]
        assert archive.getmember('profiling-records.json').size
        assert archive.getmember('profiling-summary.json').size
    return {'sst_files': len(traces), 'sst_bytes': sum(item.size for item in traces)}


def download(client, target, label):
    path = target / f'bs{label}.tar.gz'
    if path.exists():
        return {**verify_archive(path, label), 'archive_bytes': path.stat().st_size}
    preamble = f'SESSION={SESSION!r}\nREVISION={REVISIONS[CANDIDATE]!r}\nLABEL={label!r}\n'
    with tempfile.NamedTemporaryFile(dir=target, prefix=f'.bs{label}-', suffix='.part', delete=False) as stream:
        partial = Path(stream.name)
        try:
            stdin, stdout, stderr = client.exec_command('timeout --kill-after=10 480 python3 -', timeout=500)
            with ThreadPoolExecutor(max_workers=1) as executor:
                diagnostic = executor.submit(stderr.read)
                stdin.write(preamble + REMOTE)
                stdin.channel.shutdown_write()
                shutil.copyfileobj(stdout, stream, length=1024 * 1024)
                evidence = diagnostic.result()
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError('remote_trace_archive_failed')
            stream.close()
            counts = verify_archive(partial, label)
            assert counts == json.loads(evidence), 'archive_does_not_match_server'
            partial.rename(path)
        finally:
            stream.close()
            partial.unlink(missing_ok=True)
    return {**counts, 'archive_bytes': path.stat().st_size}


def main():
    import paramiko

    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    request = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    sizes = request['batch_sizes']
    assert sizes and len(set(sizes)) == len(sizes) and all(size in SIZES for size in sizes)
    assert DESTINATION.anchor and Path(DESTINATION.anchor).is_dir()
    DESTINATION.mkdir(parents=True, exist_ok=True)
    results = {}
    with paramiko.SSHClient() as client:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        connect(client)
        for size in sizes:
            for point in POINTS:
                case = f'{size}-{point}'
                results[case] = download(client, DESTINATION, case)
    print(json.dumps({'directory': str(DESTINATION), 'case_count': len(results), 'cases': results}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'error_class': type(error).__name__}))
        raise SystemExit(1)
