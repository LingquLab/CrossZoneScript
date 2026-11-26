# Copyright (c) 2026 TileXR Project.
"""Copy the two validated BS4 local-send traces from SST to green Windows."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import tarfile

SESSION = '44b9c060-a5dd-46a8-897f-e4a6fee171e2'
REVISION = 'd04fde0c3d7b3e9017ca8d43383b9183fd7a2354'
REMOTE = r'''
import gzip
import json
from pathlib import Path
import sys
import tarfile

base = Path('/mnt/share/tilexr-sim-jobs') / SESSION
cases = {
    'bs4-intra8': base / 'run-d04fde0-a1/cases/t4-local-send-a1',
    'bs4-intra4': base / 'run-d04fde0-i4-p1/cases/t4-local-send-a1',
}
for label, case in cases.items():
    report = json.loads((case / 'phase-result.json').read_text())
    assert report['candidate_commit'] == REVISION and report['stage'] == 'local-send'
    assert report['tokens'] == 4 and report['status'] == 'sst_and_checker_passed'
    assert report['checked_ranks'] == 64 and report['blocks'] == int(label[-1])
    assert report['profiling_dropped_records'] == 0
with gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', compresslevel=3) as zipped:
    with tarfile.open(fileobj=zipped, mode='w|') as out:
        for label, case in cases.items():
            def add_path(path):
                if path.is_file() and not path.is_symlink():
                    out.add(path, arcname=f'{label}/{path.relative_to(case).as_posix()}', recursive=False)
            for name in ('profiling-records.json', 'profiling-summary.json', 'phase-result.json', 'sim.log'):
                add_path(case / name)
            for folder in ('stat_trace', 'trace', 'traces'):
                directory = case / folder
                if directory.is_dir() and not directory.is_symlink():
                    for path in sorted(directory.rglob('*')):
                        add_path(path)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    assert args == {'revision': REVISION}
    profile = Path(os.environ['USERPROFILE'])
    target = profile / 'Downloads' / 'Combine-HT' / 'bs4-local-send-d04fde0'
    target.mkdir(parents=True, exist_ok=True)
    archive = target / 'sst-profiling.tar.gz'
    temporary = archive.with_suffix('.part')
    key = profile / '.ssh' / 'id_ed25519'
    command = ['ssh', '-T', '-i', str(key), '-p', '22', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=12', '-o', 'ServerAliveInterval=20',
               '-o', 'ServerAliveCountMax=3', 'root@141.61.94.26', 'timeout --kill-after=10 360 python3 -']
    script = f'SESSION={SESSION!r}\nREVISION={REVISION!r}\n' + REMOTE
    with temporary.open('wb') as output:
        copied = subprocess.run(command, input=script.encode(), stdout=output, stderr=subprocess.PIPE, timeout=385)
    (target / 'transfer.stderr').write_bytes(copied.stderr)
    assert copied.returncode == 0
    figures = {}
    with tarfile.open(temporary, 'r:gz') as bundle:
        members = bundle.getmembers()
        for cores in (4, 8):
            label = f'bs4-intra{cores}'
            data = bundle.extractfile(f'{label}/profiling-records.json')
            assert data is not None
            intervals = json.load(data)
            assert {(row['rank'], row['lane']) for row in intervals} == {
                (rank, lane) for rank in range(64) for lane in range(cores)}
            origin = min(row['begin'] for row in intervals)
            speed = 1650
            events = []
            durations = []
            for row in intervals:
                elapsed = row['elapsed_ticks'] / speed
                durations.append(elapsed)
                events.append(dict(name='local-send', cat='kernel', ph='X', pid=row['rank'], tid=row['lane'],
                                   ts=(row['begin']-origin)/speed, dur=elapsed))
            name = f'{label}.trace.json'
            (target / name).write_text(json.dumps(dict(traceEvents=events, displayTimeUnit='ns')))
            traces = [member for member in members if member.name.startswith(
                (label + '/stat_trace/', label + '/trace/', label + '/traces/'))]
            figures[label] = dict(trace=name, lanes=len(intervals), min_us=round(min(durations), 3),
                                  median_us=round(statistics.median(durations), 3), max_us=round(max(durations), 3),
                                  raw_sst_files=len(traces), raw_sst_bytes=sum(member.size for member in traces))
    temporary.replace(archive)
    with archive.open('rb') as contents:
        digest = hashlib.sha256()
        for block in iter(lambda: contents.read(1024 * 1024), b''):
            digest.update(block)
    print(json.dumps(dict(directory=r'%USERPROFILE%\Downloads\Combine-HT\bs4-local-send-d04fde0',
                          archive=archive.name, bytes=archive.stat().st_size, sha256=digest.hexdigest(), cases=figures)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
