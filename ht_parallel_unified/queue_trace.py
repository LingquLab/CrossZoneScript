"""Count retained BS32 CQ writes by route on a fixed sample of ranks."""
from collections import Counter, defaultdict
import re

GM_BYTES = 0x2000000000
CQ_BASE = 0x1A00000000
RING_BYTES = 16384 * 64
MAX_TRACE_BYTES = 16 * 1024 * 1024
RANK_SAMPLE = (0, 1, 7, 8, 23, 31, 47, 63)
TRACE_ARGS = re.compile(rb'\{[^{}]*"oriAddr_"[^{}]*\}')


def queue_trace(case):
    found = []
    for rank in RANK_SAMPLE:
        path = case / 'OUTPUT/stat_trace' / f'Host{rank}_ba_layer_trace.json'
        if not path.is_file():
            continue
        with path.open('rb') as trace:
            data = trace.read(MAX_TRACE_BYTES)
            truncated = bool(trace.read(1))
        slots = defaultdict(set)
        for match in TRACE_ARGS.finditer(data):
            if b'"WR_REQ"' not in match[0]:
                continue
            try:
                args = json.loads(match[0])
                if args['busPayloadType_'] != 'WR_REQ':
                    continue
                raw = args['oriAddr_']
                address = int(raw, 0) if isinstance(raw, str) else int(raw)
            except (KeyError, ValueError, TypeError):
                continue
            host, local_address = divmod(address, GM_BYTES)
            ring, offset = divmod(local_address - CQ_BASE, RING_BYTES)
            if host == rank and 0 <= ring < 64 * 40:
                slots[ring].add(offset // 64)
        gather = [slots[peer*40 + PRODUCER_QUEUE[peer % 8]]
                  for peer in range(rank // 8 * 8, rank // 8 * 8 + 8) if peer != rank]
        local = [slots[peer*40 + (peer % 8 % 2) * 20 + (peer % 8) // 2]
                 for peer in range(rank // 8 * 8, rank // 8 * 8 + 8) if peer != rank]
        cross = [values for ring, values in slots.items() if (ring // 40) // 8 != rank // 8]
        stages = [sum(len(values & set(range(chunk * 9, (chunk + 1) * 9))) for values in gather)
                  for chunk in range(4)]
        flags = [sum((chunk * 9 + 8) in values for values in gather) for chunk in range(4)]
        found.append([rank, truncated, stages, flags, sum(map(len, local)), sum(map(len, cross))])
    return dict(sample_columns=['rank', 'trace_truncated', 'gather_cq_by_chunk', 'gather_so_cq_by_chunk',
                                'local_return_cq', 'cross_return_cq'],
                samples=found, gather_cq_expected_per_chunk=63, gather_so_cq_expected_per_chunk=7)


def detail_gather_queues(case, tokens=32):
    return detail_queues(case, cross=False, tokens=tokens)


def detail_cross_queues(case):
    return detail_queues(case, cross=True)


def detail_queues(case, cross, tokens=32):
    expected = set(range(25 if cross else tokens + 4))
    if cross:
        inspected = set(range(18,25))
    elif tokens == 16:
        inspected = set(range(10,20))
    else:
        inspected = set(range(9,18))
    counts = Counter()
    masks = Counter()
    examples = []
    for rank in range(64):
        path = case / 'OUTPUT/stat_trace' / f'Host{rank}_ba_layer_trace.json'
        if not path.is_file():
            continue
        counts['hosts'] += 1
        gather = {peer*40 + PRODUCER_QUEUE[peer % 8]: peer for peer in range(rank//8*8, rank//8*8+8)
                  if peer != rank}
        if cross:
            peer = rank ^ 8
            ring, = (ring for ring,(kind, slots) in expected_cq_slots(rank).items()
                     if ring // 40 == peer and kind == 'cross_return' and len(slots) == 25)
            gather = {ring: peer}
        dbs = {0x1C00000000 + (ring%40//20)*0x100000000 + (peer*20 + ring%20)*64: ring
               for ring, peer in gather.items()}
        evidence = {ring: dict(sq=set(), cq=set(), bells=0, sq_head=0) for ring in gather}
        with path.open('rb') as trace:
            data = trace.read(MAX_TRACE_BYTES)
            counts['truncated_hosts'] += bool(trace.read(1))
        for match in TRACE_ARGS.finditer(data):
            if b'"WR_REQ"' not in match[0] and b'"RD_REQ"' not in match[0]:
                continue
            try:
                args = json.loads(match[0])
                event = args['busPayloadType_']
                value = args['oriAddr_']
                address = int(value, 0) if isinstance(value, str) else int(value)
            except (ValueError, TypeError, KeyError):
                continue
            host, local_address = divmod(address, GM_BYTES)
            if event == 'WR_REQ' and local_address in dbs:
                evidence[dbs[local_address]]['bells'] += 1
            if host != rank:
                continue
            if event == 'RD_REQ':
                ring, offset = divmod(local_address - 0x1900000000, RING_BYTES)
                if ring in evidence and offset // 64 in expected:
                    evidence[ring]['sq'].add(offset // 64)
            elif event == 'WR_REQ':
                ring, offset = divmod(local_address - CQ_BASE, RING_BYTES)
                if ring in evidence and offset // 64 in expected:
                    evidence[ring]['cq'].add(offset // 64)
                delta = local_address - (0x1B00000000 + 64*40*64)
                if delta >= 0 and delta % 64 == 0 and delta // 64 in evidence:
                    evidence[delta // 64]['sq_head'] += 1
        for ring, evidence_row in evidence.items():
            counts['qps'] += 1
            sq, cq = evidence_row['sq'], evidence_row['cq']
            missing = tuple(sorted(inspected - cq))
            masks[missing] += 1
            counts['sq_head_write_records'] += evidence_row['sq_head']
            counts['doorbell_write_records'] += evidence_row['bells']
            counts['sq_fetch_starts'] += len(sq)
            counts['cq_slots'] += len(cq)
            counts['qps_missing_inspected_so_cq'] += max(inspected) not in cq
            counts['missing_cq_at_recorded_sq_fetch_starts'] += len(set(missing) & sq)
            if missing and len(examples) < 6:
                examples.append([rank, gather[ring], ring%40, sorted(sq), sorted(cq),
                                 evidence_row['sq_head'], evidence_row['bells']])
    return dict(summary=counts, most_common_missing_cq_slots=masks.most_common(12),
                example_columns=['rank','peer','physical_slot','sq_fetch_start_bb','cq_bb',
                                 'sq_head_wr_req','doorbell_wr_req'], examples=examples)


def expected_cq_slots(rank, tokens=32):
    """Map expected CQ BBs to the producer's physical queues."""
    expected = {}
    rank_server = rank // 8
    for peer in range(64):
        if peer == rank:
            continue
        local = peer % 8
        peer_server = peer // 8
        if peer_server == rank_server:
            expected[peer*40 + PRODUCER_QUEUE[local]] = ('gather', set(range(tokens + 4)))
            physical_slot = local % 2 * 20 + local // 2
            payload = tokens // 2 if local == (rank % 8 + 1) % 8 else 0
            expected[peer*40 + physical_slot] = ('local_return', set(range(payload + 1)))
            continue
        group = (peer_server + 4 - rank_server % 4) % 4
        position = (0 if group == 0 else 8 + (group - 1) * 16 + peer_server // 4 * 8)
        relative = (local - rank % 8) % 8
        position += relative
        owner = relative if position >= 48 else (position + 8) % 16
        for lane in (0, 1):
            payload = (tokens * 3 // 4 if lane == 0 else tokens // 4) if peer == rank ^ 8 else 0
            ring = peer * 40 + lane * 20 + owner
            expected[ring] = ('cross_return', set(range(payload + 1)))
    return expected


def count_cq_slots(case, tokens=32):
    stage_names = ('gather', 'local_return', 'cross_return')
    stages = {name: Counter() for name in stage_names}
    total = Counter()
    samples = []
    for rank in range(64):
        path = case / 'OUTPUT/stat_trace' / f'Host{rank}_ba_layer_trace.json'
        if not path.is_file():
            continue
        with path.open('rb') as trace:
            data = trace.read(MAX_TRACE_BYTES)
            total['truncated_hosts'] += bool(trace.read(1))
        total['hosts'] += 1
        traces = defaultdict(set)
        for match in TRACE_ARGS.finditer(data):
            if b'"WR_REQ"' not in match[0]:
                continue
            try:
                args = json.loads(match[0])
                if args['busPayloadType_'] != 'WR_REQ':
                    continue
                value = args['oriAddr_']
                address = int(value, 0) if isinstance(value, str) else int(value)
            except (KeyError, ValueError, TypeError):
                continue
            host, local_address = divmod(address, GM_BYTES)
            ring, offset = divmod(local_address - CQ_BASE, RING_BYTES)
            if host == rank and 0 <= ring < 64*40:
                traces[ring].add(offset // 64)
                total['wr_req_cq_records'] += 1
        for ring, (name, slots) in expected_cq_slots(rank, tokens).items():
            found = traces.pop(ring, set())
            missing = slots - found
            summary = stages[name]
            summary['expected'] += len(slots)
            summary['seen'] += len(slots & found)
            summary['unexpected_slots'] += len(found - slots)
            summary['qps_missing'] += bool(missing)
            if missing and len(samples) < 6:
                samples.append([rank, ring//40, ring%40, name, sorted(missing), sorted(found)])
        total['unexpected_qp_slots'] += sum(map(len, traces.values()))
    return dict(totals=total, stages=stages, first_missing=samples)


def cross_so_arrival(case):
    offsets = json.loads((case/'fixture-data/trace-offsets.json').read_text())
    counts = Counter()
    examples = []
    missing_lane0 = []
    missing_rows = []
    for rank in range(64):
        path = case/'OUTPUT/stat_trace'/f'Host{rank}_ba_layer_trace.json'
        if not path.is_file():
            continue
        counts['hosts'] += 1
        workspace = offsets[rank]['workspace']
        completion = offsets[rank]['offset_map']['completed']
        paired_gateway = rank ^ 8
        targets = {}
        for gateway in range(64):
            if gateway // 8 == rank // 8:
                continue
            for lane in range(2):
                addr = workspace + completion + ((gateway*4+3)*2+lane)*512
                targets[addr] = (gateway, lane)
        returned = offsets[rank]['return_offset']
        payload_targets = {workspace + returned + (token*8 + paired_gateway//8)*512: token
                           for token in range(32)}
        payload_writes = set()
        with path.open('rb') as trace:
            data = trace.read(MAX_TRACE_BYTES)
            counts['truncated_hosts'] += bool(trace.read(1))
        events = defaultdict(set)
        for match in TRACE_ARGS.finditer(data):
            if b'"WR_' not in match[0]:
                continue
            try:
                args = json.loads(match[0])
                event = args['busPayloadType_']
                if event not in ('WR_REQ', 'WR_DATA', 'WR_DATA_RESP'):
                    continue
            except (KeyError, ValueError):
                continue
            for field in ('oriAddr_', 'wrAddr_'):
                value = args.get(field)
                try:
                    addr = int(value, 0) if isinstance(value, str) else int(value)
                except (TypeError, ValueError):
                    continue
                if addr in targets:
                    events[targets[addr]].add(event)
                if event == 'WR_DATA' and addr in payload_targets:
                    payload_writes.add(payload_targets[addr])
        for role, pair in (('paired_lane0', (paired_gateway, 0)), ('paired_lane1', (paired_gateway, 1))):
            for event in events.get(pair, set()):
                counts[f'{role}_{event}'] += 1
        if 'WR_DATA' not in events.get((paired_gateway,0), ()):
            missing_lane0.append([rank, paired_gateway])
        counts['cross_rows_wr_data'] += len(payload_writes)
        if len(payload_writes) != 32 and len(missing_rows) < 6:
            missing_rows.append([rank, paired_gateway, sorted(set(range(32)) - payload_writes)])
        counts['remote_slots_wr_data'] += sum('WR_DATA' in observed for observed in events.values())
        if len(examples) < 3:
            examples.append([rank, paired_gateway, sorted(events.get((paired_gateway,0), ())),
                             sorted(events.get((paired_gateway,1), ()))])
    return dict(counts=counts, sample_columns=['receiver','gateway','lane0_events','lane1_events'],
                examples=examples, missing_lane0=missing_lane0[:8], missing_payload_rows=missing_rows)
