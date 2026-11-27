# Copyright (c) 2026 TileXR Project.
"""Download the validated a4885e1 profiling matrix to green Windows."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

REVISION = 'a4885e131bee353f47e74a6e90de2e16d7dcbba1'
SESSION = '6992376d-c5e1-4c7e-bb0f-c77cdb096bf5'
PREVIOUS_SESSION = '44b9c060-a5dd-46a8-897f-e4a6fee171e2'
CLOCK_RECEIPT = 'green-47bc1c1b34ed4685a7b57d9c3c574111'
TICKS_PER_US = 1650

VARIANTS = (
    ('a1', 8, 1, 'local-send', 512),
    ('a1', 8, 1, 'gateway-reduce', 512),
    ('a1', 8, 1, 'local-return', 512),
    ('a1', 8, 1, 'inter-server-send', 1024),
    ('a1', 8, 1, 'final-independent', 512),
    ('a1', 8, 1, 'joint-final', 2560),
    ('i4-p1', 4, 1, 'local-send', 256),
    ('i4-p1', 4, 1, 'joint-final', 2304),
    ('i4-p2', 4, 2, 'joint-final', 2304),
    ('i8-p2', 8, 2, 'inter-server-send', 1024),
    ('i8-p2', 8, 2, 'joint-final', 2560),
)
FILES = ('profiling-records.json', 'profiling-summary.json', 'phase-result.json')

REMOTE = r'''
import gzip
from pathlib import Path
import sys
import tarfile

with gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', compresslevel=3) as zipped:
    with tarfile.open(fileobj=zipped, mode='w|') as bundle:
        for label, source in SOURCES.items():
            case = Path(source)
            for name in FILES:
                path = case / name
                assert path.is_file() and not path.is_symlink(), str(path)
                bundle.add(path, arcname=f'{label}/{name}', recursive=False)
'''


def cases():
    for tokens in (4, 32, 128):
        for variant, cores, slots, stage, lanes in VARIANTS:
            reused_bs4 = tokens == 4 and (
                (variant == 'a1' and stage != 'joint-final') or variant == 'i4-p2'
            )
            session = PREVIOUS_SESSION if reused_bs4 else SESSION
            label = f'bs{tokens}/intra{cores}-peer{slots}/{stage}'
            source = f'/mnt/share/tilexr-sim-jobs/{session}/run-a4885e1-{variant}/cases/t{tokens}-{stage}-a1'
            expected = dict(candidate_commit=REVISION, tokens=tokens, intra_cores=cores,
                            peer_slots=slots, stage=stage, profiling_lanes=lanes, checked_ranks=64,
                            status='sst_and_checker_passed', profiling_dropped_records=0,
                            profiling_clock_receipt=CLOCK_RECEIPT)
            yield label, source, expected


def pack_traces(download, output, matrix):
    counts = {f'bs{tokens}': 0 for tokens in (4, 32, 128)}
    trace_lanes = 0
    with tarfile.open(download, 'r:gz') as raw, ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for label, _, expected in matrix:
            blobs = {name: raw.extractfile(f'{label}/{name}').read() for name in FILES}
            report = json.loads(blobs['phase-result.json'])
            for key, value in expected.items():
                assert report.get(key) == value, (label, key, report.get(key), value)
            summary = json.loads(blobs['profiling-summary.json'])
            intervals = json.loads(blobs['profiling-records.json'])
            lanes = expected['profiling_lanes']
            lanes_per_rank = lanes // 64
            assert len(intervals) == lanes, label
            assert {(row['rank'], row['lane']) for row in intervals} == {
                (rank, lane) for rank in range(64) for lane in range(lanes_per_rank)
            }, label
            peak = max(row['elapsed_ticks'] for row in intervals)
            assert peak == summary['profiling_max_lane_ticks'] == report['profiling_max_lane_ticks'], label
            assert math.isclose(peak / TICKS_PER_US, report['profiling_operator_us']), label
            origin = min(row['begin'] for row in intervals)
            events = []
            for row in intervals:
                elapsed = row['end'] - row['begin']
                assert elapsed == row['elapsed_ticks'] and elapsed > 0, label
                events.append(dict(name=expected['stage'], cat='kernel', ph='X', pid=row['rank'],
                                   tid=row['lane'], ts=(row['begin'] - origin) / TICKS_PER_US,
                                   dur=elapsed / TICKS_PER_US))
            for name, blob in blobs.items():
                archive.writestr(f'{label}/{name}', blob)
            archive.writestr(f'{label}/profiling.trace.json', json.dumps({'traceEvents': events}))
            counts[f"bs{expected['tokens']}"] += 1
            trace_lanes += len(events)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(matrix) * (len(FILES) + 1)
    return counts, trace_lanes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    assert args == {'revision': REVISION}
    profile = Path(os.environ['USERPROFILE'])
    target = profile / 'Downloads' / 'Combine-HT' / 'profiling-a4885e1'
    target.mkdir(parents=True, exist_ok=True)
    archive = target / 'sst-profiling-a4885e1.zip'
    command = ['ssh', '-T', '-i', str(profile / '.ssh' / 'id_ed25519'), '-p', '22',
               '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'ConnectTimeout=12', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3',
               'root@141.61.94.26', 'timeout --kill-after=10 90 python3 -']
    matrix = list(cases())
    sources = {label: source for label, source, _ in matrix}
    script = f'SOURCES={sources!r}\nFILES={FILES!r}\n' + REMOTE
    with tempfile.TemporaryDirectory(dir=target) as temporary:
        download = Path(temporary) / 'raw.tar.gz'
        staged_archive = Path(temporary) / archive.name
        with download.open('wb') as output:
            copied = subprocess.run(command, input=script.encode(), stdout=output,
                                    stderr=subprocess.PIPE, timeout=105)
        if copied.returncode:
            (target / 'transfer.stderr').write_bytes(copied.stderr)
            raise SystemExit(f'SSH transfer exit {copied.returncode}')
        counts, lanes = pack_traces(download, staged_archive, matrix)
        staged_archive.replace(archive)
    print(json.dumps(dict(archive=str(archive), bytes=archive.stat().st_size,
                          cases=counts, trace_events=lanes)))


if __name__ == '__main__':
    main()
