"""Count routed doorbell and CQ writes in retained local-send SST traces."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

from bootstrap_green import REVISIONS, SESSION

TRACE_ARGS = re.compile(rb'\{[^{}]*"busPayloadType_"[^{}]*\}')
TIMESTAMP = re.compile(rb'"ts"\s*:\s*(-?\d+(?:\.\d+)?)')
REVISION = REVISIONS['a50893f']


def writes(path):
    data = path.read_bytes()
    assert len(data) < 16 * 1024 * 1024
    timestamps = iter(TIMESTAMP.finditer(data))
    following = next(timestamps, None)
    previous = None
    for match in TRACE_ARGS.finditer(data):
        while following and following.start() < match.start():
            previous, following = following, next(timestamps, None)
        if previous is None or match.start() - previous.end() > 4096 or b'"WR_REQ"' not in match[0]:
            continue
        args = json.loads(match[0])
        addr = args.get('oriAddr_', args.get('wrAddr_'))
        if args.get('busPayloadType_') == 'WR_REQ' and addr is not None:
            yield float(previous[1]), int(addr, 0) if isinstance(addr, str) else addr


def inspect(case, runtime):
    stats = Counter()
    ranks = []
    for rank in range(64):
        local = rank % 8
        queues = {peer: runtime.queue_addresses(rank, rank - local + peer, peer,
                  port0_die_mode='chip_topo') for peer in range(8) if peer != local}
        bells = {q['db'] % runtime.GM_BYTES: peer for peer, q in queues.items()}
        seen = {peer: set() for peer in queues}
        last = {peer: 0 for peer in queues}
        trace = case / 'OUTPUT' / 'stat_trace' / f'Host{rank}_ba_layer_trace.json'
        bell_counts = Counter()
        for stamp, addr in writes(trace):
            peer = bells.get(addr % runtime.GM_BYTES)
            if peer is not None:
                bell_counts[peer] += 1
                last[peer] = max(last[peer], stamp)
            for peer, queue in queues.items():
                offset = addr - queue['cq']
                if 0 <= offset < runtime.DEPTH * runtime.BB_BYTES:
                    seen[peer].add(offset // runtime.BB_BYTES)
                    last[peer] = max(last[peer], stamp)
                    break
        marks = sum(len(indices) for indices in seen.values())
        writes_count = sum(bell_counts.values())
        stats['cq_entries_observed'] += marks
        stats['doorbell_writes_observed'] += writes_count
        stats['queues_with_cq_and_bells'] += sum(bool(seen[peer] and bell_counts[peer]) for peer in queues)
        ranks.append({'rank': rank, 'cq_entries': marks, 'doorbell_writes': writes_count,
                      'last_queue_write': max(last.values())})
    result = json.loads((case / 'phase-result.json').read_text())
    assert result['candidate_commit'] == REVISION
    return {'case': case.name, 'returncode': result['returncode'], 'counts': stats,
            'earliest_rank_frontiers': sorted(ranks, key=lambda row: row['last_queue_write'])[:5],
            'latest_rank_frontiers': sorted(ranks, key=lambda row: row['last_queue_write'], reverse=True)[:5]}


def host_main(args):
    import socket

    assert socket.gethostname().split('.')[0] == 'host-94-18'
    root = Path('/mnt/share/tilexr-sim-jobs') / SESSION / 'run-a50893f-host18-a1'
    sys.path.insert(0, str(root / 'source/tools/unitest/simulator/experiments/inter_server_send/sst'))
    import runtime

    names = args['cases']
    assert names and len(names) <= 4
    reports = []
    for name in names:
        assert re.fullmatch(r'cores[1248]-chunk(?:16|32|64|128)-a[0-9]+', name)
        reports.append(inspect(root / 'cases' / name, runtime))
    print(json.dumps({'queue_frontiers': reports}))


if __name__ == '__main__' and len(sys.argv) > 1:
    from green import execute
    from transport import connect
    import tempfile

    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    assert args['host'] == '18'
    with tempfile.TemporaryDirectory() as local, connect(args['host'], args['auth']) as client:
        # Only the two constants and host-side declarations are needed on Linux.
        source = Path(__file__).read_text(encoding='utf-8')
        source = source.replace('from bootstrap_green import REVISIONS, SESSION',
            'SESSION = ' + repr(SESSION) + '\nREVISIONS = ' + repr(REVISIONS))
        source = source[:source.index("if __name__ == '__main__'")] + '\n\nmain = host_main\n'
        import green
        green.HERE = Path(local)
        (Path(local) / 'queue_frontier.py').write_text(source)
        code, result = execute(client, 'queue_frontier.py', args, 100, Path(local))
        print(json.dumps(result, separators=(',', ':')))
        raise SystemExit(code)
