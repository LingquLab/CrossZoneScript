"""Count CQ slots by the candidate's physical queue bindings."""
import json


def expected_cq_slots(rank, tokens):
    queues = {}
    for peer in range(64):
        if peer == rank:
            continue
        local = peer % 8
        if peer // 8 == rank // 8:
            physical = PRODUCER_QUEUE[local]
            ring = peer * 40 + physical
            gathered = tokens + 4
            returned = (tokens // 2 if local == (rank % 8 + 1) % 8 else 0) + 1
            queues[ring] = [('gather', range(gathered)), ('local_return', range(gathered, gathered + returned))]
            continue
        group = (peer // 8 + 4 - rank // 8 % 4) % 4
        position = (0 if group == 0 else 8 + (group - 1) * 16 + peer // 32 * 8)
        relative = (local - rank % 8) % 8
        position += relative
        owner = relative if position >= 48 else (position + 8) % 16
        for lane in (0, 1):
            physical = lane * 20 + owner
            payload = (tokens * 3 // 4 if lane == 0 else tokens // 4) if peer == rank ^ 8 else 0
            queues[peer * 40 + physical] = [('cross_return', range(payload + 1))]
    return queues


def count_candidate_cq(case, tokens):
    phases = {name: Counter() for name in ('gather', 'local_return', 'cross_return')}
    totals = Counter()
    details = []
    missing = []
    for rank in range(64):
        path = case / 'OUTPUT/stat_trace' / f'Host{rank}_ba_layer_trace.json'
        if not path.is_file():
            continue
        with path.open('rb') as stream:
            raw = stream.read(MAX_TRACE_BYTES)
            totals['truncated_hosts'] += bool(stream.read(1))
        totals['hosts'] += 1
        expected = expected_cq_slots(rank, tokens)
        cq, fetch, bells = defaultdict(set), defaultdict(set), Counter()
        db_rings = {0x1C00000000 + physical // 20 * 0x100000000 + (peer * 20 + physical % 20) * 64: ring
                    for ring in expected for peer, physical in (divmod(ring, 40),)}
        for match in TRACE_ARGS.finditer(raw):
            if b'"WR_REQ"' not in match[0] and b'"RD_REQ"' not in match[0]:
                continue
            args = json.loads(match[0])
            value = args['oriAddr_']
            address = int(value, 0) if isinstance(value, str) else int(value)
            host, local = divmod(address, GM_BYTES)
            if args['busPayloadType_'] == 'WR_REQ':
                if local in db_rings:
                    bells[db_rings[local]] += 1
                ring, offset = divmod(local - CQ_BASE, RING_BYTES)
                if host == rank and 0 <= ring < 64 * 40:
                    cq[ring].add(offset // 64)
            elif args['busPayloadType_'] == 'RD_REQ' and host == rank:
                ring, offset = divmod(local - 0x1900000000, RING_BYTES)
                if ring in expected:
                    fetch[ring].add(offset // 64)
        for ring, entries in expected.items():
            peer, physical = divmod(ring, 40)
            found = cq.pop(ring, set())
            for name, slots in entries:
                present = found.intersection(slots)
                absent = sorted(set(slots) - present)
                phase = phases[name]
                phase.update(expected=len(slots), observed=len(present), qps=1, qps_missing=int(bool(absent)))
                row = [rank, peer, physical, name, slots.start, len(slots), len(present), absent]
                details.append(row)
                if absent and len(missing) < 6:
                    missing.append(row + [bells[ring], sorted(fetch[ring])])
            totals['unexpected_slots'] += len(found.difference(*(slots for _, slots in entries)))
        totals['unexpected_slots'] += sum(map(len, cq.values()))
    evidence = dict(totals=totals, stages=phases, first_missing=missing)
    (case / 'tunable-cq-slots.json').write_text(json.dumps({
        **evidence, 'columns': ['rank', 'peer', 'physical_slot', 'stage', 'first_slot', 'expected', 'observed', 'missing'],
        'qps': details,
    }, separators=(',', ':')))
    return evidence
