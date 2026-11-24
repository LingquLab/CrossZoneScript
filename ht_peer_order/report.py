"""Summarize paired SST receipts in CSV and JSON for a single BS."""
import argparse
import csv
import json
from pathlib import Path
import statistics

from fixture import BASE


OK = 'sst_and_checker_passed'
COLUMNS = ('seed', 'route_sha256', 'payload_wqes', 'so_wqes', 'rotate_status', 'peer_status',
           'rotate_us', 'peer_us', 'peer_minus_rotate_us', 'peer_minus_rotate_percent')


def read_rounds(paths, tokens):
    rounds = {}
    object_hash = None
    for path in paths:
        envelopes = json.loads(path.read_text())
        if isinstance(envelopes, dict):
            envelopes = [envelopes]
        for envelope in envelopes:
            if 'summary' in envelope:
                envelope = json.loads(envelope['summary'].split('\n', 1)[1])
            if envelope.get('tokens') != tokens:
                continue
            if object_hash is None:
                object_hash = envelope['object_sha256']
            assert envelope['object_sha256'] == object_hash
            for result in envelope['results']:
                rounds.setdefault(result['seed'], {})[result['mode']] = result
    return rounds, object_hash


def compare(rounds, expected):
    for seed in expected:
        pair = rounds.get(seed, {})
        rotate, peer = (pair.get(mode, {}) for mode in ('rotate', 'peer'))
        sample = rotate or peer
        payload = sum(sample['payload_by_lane']) if sample else None
        row = dict(seed=seed, route_sha256=sample.get('route_sha256'), payload_wqes=payload,
                   so_wqes=sample['expected_cqe'] - payload if sample else None,
                   rotate_status=rotate.get('status'), peer_status=peer.get('status'))
        if rotate.get('status') == OK and peer.get('status') == OK:
            for field in ('route_sha256', 'payload_by_lane', 'expected_cqe', 'guard_count'):
                assert rotate[field] == peer[field], (seed, field)
            for field in ('checked', 'exports_sha256'):
                if field in rotate and field in peer:
                    assert rotate[field] == peer[field], (seed, field)
            assert rotate['cqe'] == peer['cqe'] == rotate['expected_cqe']
            delta = peer['sim_us'] - rotate['sim_us']
            row.update(rotate_us=rotate['sim_us'], peer_us=peer['sim_us'],
                       peer_minus_rotate_us=delta, peer_minus_rotate_percent=100*delta/rotate['sim_us'])
        yield row
    # The caller keeps missing and failed rows in the CSV.


def stats(rows):
    result = {}
    for field in COLUMNS[6:]:
        values = [row[field] for row in rows if field in row]
        if values:
            result[field] = dict(min=min(values), median=statistics.median(values),
                                 mean=statistics.mean(values), max=max(values))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tokens', type=int, choices=(32, 64))
    parser.add_argument('--pairs', type=int, choices=range(1, 11), default=10)
    parser.add_argument('output', type=Path)
    parser.add_argument('receipts', type=Path, nargs='+')
    args = parser.parse_args()
    rounds, object_hash = read_rounds(args.receipts, args.tokens)
    rows = list(compare(rounds, range(BASE, BASE+args.pairs)))
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output/'rounds.csv').open('w') as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(tokens=args.tokens, object_sha256=object_hash, requested_pairs=len(rows),
                   valid_pairs=sum('rotate_us' in row for row in rows),
                   peer_faster=sum(row.get('peer_minus_rotate_us', 0) < 0 for row in rows),
                   rotate_faster=sum(row.get('peer_minus_rotate_us', 0) > 0 for row in rows),
                   statistics=stats(rows))
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
