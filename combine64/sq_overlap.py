"""Find overlapping payload SQ submissions for same-owner peer pairs in retained SST traces."""
from collections import Counter
import json
from pathlib import Path
import re
import struct
import sys

TRACE_ARGS = re.compile(rb'\{[^{}]*"busPayloadType_"[^{}]*\}')
TIMESTAMP = re.compile(rb'"ts"\s*:\s*(-?\d+(?:\.\d+)?)')
TRACE_LIMIT = 16 * 1024 * 1024
WORLD, OWNERS, SLOTS_PER_DIE = 64, 8, 20


def first_writes(path, rank, bells, cqes, gm_bytes):
    with path.open('rb') as stream:
        data = stream.read(TRACE_LIMIT)
        truncated = bool(stream.read(1))
    timestamps = TIMESTAMP.finditer(data)
    following = next(timestamps, None)
    previous = None
    observed_bells, observed_cqes = {}, {}
    for match in TRACE_ARGS.finditer(data):
        while following and following.start() < match.start():
            previous = following
            following = next(timestamps, None)
        if previous is None or match.start() - previous.end() > 4096 or b'"WR_REQ"' not in match[0]:
            continue
        args = json.loads(match[0])
        if args.get('busPayloadType_') != 'WR_REQ':
            continue
        raw_address = args.get('oriAddr_', args.get('wrAddr_'))
        if raw_address is None:
            continue
        address = int(raw_address, 0) if isinstance(raw_address, str) else int(raw_address)
        host, local = divmod(address, gm_bytes)
        if local in bells:
            observed_bells.setdefault(bells[local], float(previous[1]))
        if host == rank and address in cqes:
            observed_cqes.setdefault(cqes[address], float(previous[1]))
    return observed_bells, observed_cqes, truncated


def inspect(root, case, asset):
    sys.path.insert(0, str(root/'source/tools/unitest/simulator/experiments/inter_server_send/sst'))
    import runtime

    counts = Counter()
    proven_owners = set()
    examples = []
    identity = json.loads((asset/'identity.json').read_text())
    assert identity['peer_slots'] == 2 and identity['port_jetties'] == [1, 1]
    assert identity['port0_die_mode'] == 'chip_topo'
    chunks = identity['chunks']
    for rank in range(WORLD):
        trace = case/'OUTPUT/stat_trace'/f'Host{rank}_ba_layer_trace.json'
        if not trace.is_file():
            continue
        counts['trace_files'] += 1
        name = f'rank{rank:02}'
        schedule = (asset/'metadata'/f'{name}.schedule').read_bytes()
        stride = len(schedule) // WORLD
        order = struct.unpack_from('<64I', schedule, rank * stride)
        raw_ranges = (asset/'loader/input_bin'/name/'ranges.bin').read_bytes()
        pairs, bells, cqes = [], {}, {}
        for first in range(0, WORLD-8, 2):
            owner = first // 2 % OWNERS
            peers = order[first:first+2]
            assert len(peers) == 2 and all(peer // 8 != rank // 8 for peer in peers)
            ranges = [struct.unpack_from('<2i', raw_ranges, peer * chunks * 8) for peer in peers]
            if any(begin >= end for begin, end in ranges):
                continue
            pairs.append((owner, peers))
            for peer in peers:
                addresses = runtime.queue_addresses(rank, peer, owner, OWNERS, OWNERS, 'chip_topo')
                bells[addresses['db'] % runtime.GM_BYTES] = peer
                cqes[addresses['cq']] = peer
        if not pairs:
            continue
        counts['chunk0_payload_pairs'] += len(pairs)
        bell, cq, truncated = first_writes(trace, rank, bells, cqes, runtime.GM_BYTES)
        counts['truncated_files'] += truncated
        for owner, peers in pairs:
            if not all(peer in bell and peer in cq and bell[peer] < cq[peer] for peer in peers):
                continue
            counts['pairs_with_both_submit_and_cqe'] += 1
            last_submit = max(bell[peer] for peer in peers)
            first_cq = min(cq[peer] for peer in peers)
            if last_submit < first_cq:
                counts['pairs_overlapping'] += 1
                proven_owners.add((rank, owner))
                if len(examples) < 4:
                    examples.append({'rank': rank, 'owner': owner, 'peers': peers,
                                     'second_doorbell_ts': last_submit, 'first_cqe_write_ts': first_cq})
    return {'counts': counts, 'rank_owner_pairs_with_overlap': len(proven_owners), 'examples': examples}


def main(request):
    assert request['host'] == 'host-94-18' and request['bs'] == 4
    root = Path('/mnt/share/tilexr-sim-jobs/1a5bdfed-7c8c-47ca-b7b6-827c1777651a/run-c030900-host18-a1')
    journal = json.loads((root/'matrix-results.json').read_text())
    row = journal['4-inter-16x16']
    assert row['candidate_commit'] == 'c03090090b9abf3ccf3b5ce51fb3aaf3d9d3cd32'
    assert row['status'] == 'sst_and_checker_passed' and row['peer_slots'] == 2
    case = Path(row['case'])
    assert case.parent == root/'cases' and case.name == 't4-inter-server-send-a4'
    report = inspect(root, case, root/'assets/t4-intra8-peer2')
    print(json.dumps(report,separators=(',',':')))
    return 0
