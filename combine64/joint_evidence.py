# Copyright (c) 2026 TileXR Project.
"""Correlate the pinned joint snapshot's queue ownership and missing stage flags."""
from collections import Counter
import json
from pathlib import Path
import re
import struct
import sys

SUPPORT = '/work/source/tools/unitest/simulator/experiments/inter_server_send/sst'
sys.path.insert(0, SUPPORT)
from addressed_exports import reconstruct_die_output
from runtime import PHYSICAL_QPS, WORLD

MAGIC = 2026
CHUNKS = 4
STRIPE = 512


def i64(data, offset):
    return struct.unpack_from('<q', data, offset)[0]


def main(case):
    report = json.loads((case / 'phase-result.json').read_text())
    assert report['candidate_commit'] == 'd04fde0c3d7b3e9017ca8d43383b9183fd7a2354'
    assert report['status'] == 'diagnostic_snapshot' and report['tokens'] == 4
    assert report['stage'] == 'joint-final' and report['attempt'] == 2
    decoder = Path(json.loads((case / 'sst-environment.json').read_text())['decoder_library'])
    groups = 4 // report['peer_slots']
    layout = json.loads((case / 'export-layout.json').read_text())
    routes = json.loads((case.parent.parent / 'assets' /
        f"t4-intra{report['intra_cores']}-peer{report['peer_slots']}" / 'routes.json').read_text())
    counters = Counter()
    missing_rows = []
    missing_stages = Counter()
    missing_links = Counter()
    outstanding = Counter()
    queue_samples = []
    readiness = {}
    flags = {}
    queues = {}

    for row in layout['ranks']:
        rank = row['rank']
        folder = case / 'fixture-data' / f'rank{rank:02}'
        data = {}
        for name in ('gateway_ready', 'completed', 'local_ready', 'sq_head', 'sq_tail', 'gather'):
            region = row['exports'][name]
            sources = [case / 'bin' / f'david_{rank}_die{die}_{name}.bin' for die in (1, 2)]
            data[name] = reconstruct_die_output(
                sources, region['addr'] - row['sys_va_base_addr'], region['size'], decoder)
        flags[rank] = (data['local_ready'], data['completed'])
        queues[rank] = (data['sq_head'], data['sq_tail'])
        ready = data['gateway_ready']
        readiness[rank] = []
        for group in range(groups):
            for chunk in range(CHUNKS):
                index = group * CHUNKS + chunk
                missing = sum(i64(ready, reducer * STRIPE + index * 8) != MAGIC for reducer in range(8))
                if missing:
                    missing_stages[f'g{group}/c{chunk}'] += missing
                readiness[rank].append(missing)
        wanted = (folder / 'golden-gather.bin').read_bytes()
        blank = struct.pack('<f', -17.0) * 128
        for index in range(len(wanted) // STRIPE):
            offset = index * STRIPE
            expected = wanted[offset:offset + STRIPE]
            if expected != blank:
                observed = data['gather'][offset:offset + STRIPE]
                counters['gathered' if observed == expected else 'gather_missing'] += 1
                if observed != expected:
                    route = routes[index // 6]
                    expert = route['experts'][index % 6]
                    sender = expert // 4
                    assert sender // 8 == rank // 8 and sender != rank
                    missing_rows.append((sender, rank, route['token']))

    for sender, (heads, tails) in queues.items():
        for receiver in range(WORLD):
            if sender == receiver:
                continue
            local = sender // 8 == receiver // 8
            if local:
                qp = receiver % 8
                slots = [16 + qp // 2 + (qp % 2) * 20]
            else:
                ordered = [rank for rank in range(WORLD) if rank // 8 != sender // 8]
                ordinal = ordered.index(receiver)
                slot = ordinal % (16 * report['peer_slots']) // report['peer_slots']
                slots = [slot, slot + 20]
            for lane, slot in enumerate(slots):
                offset = (receiver * PHYSICAL_QPS + slot) * 64
                head, = struct.unpack_from('<I', heads, offset)
                tail, = struct.unpack_from('<I', tails, offset)
                delta = head - tail
                domain = 'local' if local else 'inter'
                if delta > 0:
                    outstanding[f'{domain}/delta{delta}'] += 1
                for chunk in range(CHUNKS):
                    if local:
                        value = i64(flags[receiver][0], (sender % 8) * STRIPE + chunk * 8)
                        group = groups - 1
                    else:
                        value = i64(flags[receiver][1], ((sender * CHUNKS + chunk) * 2 + lane) * STRIPE)
                        group = ordinal // (16 * report['peer_slots'])
                    if value == MAGIC:
                        continue
                    received = readiness[sender][group * CHUNKS + chunk] == 0
                    key = '/'.join((domain, f'c{chunk}', 'gateway_ready' if received else 'gateway_wait',
                                    'pending' if delta > 0 else 'drained'))
                    missing_links[key] += 1
                    if received and delta > 0 and len(queue_samples) < 12:
                        queue_samples.append([sender, receiver, slot, chunk, head, tail])
    gather_dependencies = Counter()
    gather_samples = []
    for sender, receiver, chunk in missing_rows:
        within = receiver % 8
        peers_per_core = 8 // report['intra_cores']
        begin = within // peers_per_core * peers_per_core
        owned = range(receiver // 8 * 8 + begin, receiver // 8 * 8 + begin + peers_per_core)
        previous = chunk == 0 or all(
            i64(flags[peer][0], (sender % 8) * STRIPE + (chunk - 1) * 8) == MAGIC for peer in owned)
        pending_peers = []
        for peer in owned:
            if peer == sender:
                continue
            qp = peer % 8
            physical = 16 + qp // 2 + (qp % 2) * 20
            offset = (peer * PHYSICAL_QPS + physical) * 64
            head = struct.unpack_from('<I', queues[sender][0], offset)[0]
            tail = struct.unpack_from('<I', queues[sender][1], offset)[0]
            if head > tail:
                pending_peers.append([peer, physical, head, tail])
        key = '/'.join((f'c{chunk}', 'return_visible' if previous else 'return_missing',
                        'pending_sq' if pending_peers else 'no_pending_sq'))
        gather_dependencies[key] += 1
        if previous and pending_peers and len(gather_samples) < 12:
            gather_samples.append([sender, receiver, chunk, pending_peers])
    log = (case / 'sim.log').read_text(errors='replace')
    cq = re.findall(r'Sim: ([0-9]+)[(]cycle[)][^\n]*WrCQEResp', log)
    summary = dict(stop_at_us=report['stop_at_us'], last_cq_cycle=int(cq[-1]) if cq else None,
                   gather=dict(counters), missing_gateway_by_stage=dict(missing_stages),
                   missing_gather_dependencies=dict(gather_dependencies),
                   missing_gather_examples=gather_samples,
                   missing_links=dict(missing_links), pending_sq_delta=dict(outstanding),
                   examples_sender_receiver_slot_chunk_head_tail=queue_samples)
    print(json.dumps(summary, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(Path(sys.argv[1])))
