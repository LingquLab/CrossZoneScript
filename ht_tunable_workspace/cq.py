"""Count CQ slots by the candidate's physical queue bindings."""
import json


def expected_cq_slots(rank, tokens, mode=None):
    queues = {}
    steps = len(sync_steps(tokens, mode))
    ends = chunk_ends(tokens)
    intervals = list(zip([0] + ends[:-1], ends))
    direct_slots = (8, 28, 9, 29, 10, 30, 11)
    for peer in range(64):
        if peer == rank:
            continue
        local = peer % 8
        if peer // 8 == rank // 8:
            physical = PRODUCER_QUEUE[local]
            ring = peer * 40 + physical
            gathered = tokens + len(ends)
            returned = (tokens // 2 if local == (rank % 8 + 1) % 8 else 0) + steps
            queues[ring] = [('gather', range(gathered))]
            if mode == 'direct':
                direct = direct_slots[local - int(local > rank % 8)]
                queues[peer * 40 + direct] = [('local_return', range(returned))]
            else:
                queues[ring].append(('local_return', range(gathered, gathered + returned)))
            continue
        signals = cross_signal_chunks(rank, peer, tokens, mode)
        counts = Counter()
        for chunk, (first, end) in enumerate(intervals):
            owner = (local - rank % 8) % 8 if mode == 'direct' else legacy_owner(rank, peer, chunk)
            for lane in (0, 1):
                physical = lane * 20 + owner
                rare_lane_rows = end // 4 - first // 4
                payload = (end - first - rare_lane_rows if lane == 0 else rare_lane_rows) if peer == rank ^ 8 else 0
                counts[physical] += payload + int(chunk in signals)
        for physical, count in counts.items():
            if count:
                queues[peer * 40 + physical] = [('cross_return', range(count))]
    return queues


def count_candidate_cq(case, tokens, expected_for_rank=None, mode=None):
    phases = {name: Counter() for name in ('gather', 'local_return', 'cross_return')}
    totals = Counter()
    details = []
    missing = {name: [] for name in phases}
    for rank in range(64):
        path = case / 'OUTPUT/stat_trace' / f'Host{rank}_ba_layer_trace.json'
        if not path.is_file():
            continue
        with path.open('rb') as stream:
            raw = stream.read(trace_limit(tokens, mode))
            totals['truncated_hosts'] += bool(stream.read(1))
        totals['hosts'] += 1
        expected = expected_for_rank[rank] if expected_for_rank is not None else expected_cq_slots(rank, tokens, mode)
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
                if absent and len(missing[name]) < 2:
                    sample = [*row[:7], absent[:32], bells[ring], sorted(fetch[ring])[:32]]
                    missing[name].append(sample)
            totals['unexpected_slots'] += len(found.difference(*(slots for _, slots in entries)))
        totals['unexpected_slots'] += sum(map(len, cq.values()))
    evidence = dict(totals=totals, stages=phases, first_missing=[row for rows in missing.values() for row in rows])
    (case / 'tunable-cq-slots.json').write_text(json.dumps({
        **evidence, 'columns': ['rank', 'peer', 'physical_slot', 'stage', 'first_slot', 'expected', 'observed', 'missing'],
        'qps': details,
    }, separators=(',', ':')))
    return evidence


def pipeline_missing_arrivals(case, tokens, mode, missing):
    layout = json.loads((case / 'fixture-data/trace-offsets.json').read_text())
    ends = chunk_ends(tokens)
    intervals = list(zip([0] + ends[:-1], ends))
    steps = sync_steps(tokens, mode)
    result = []
    for sender, peer, physical, stage, origin, _, _, absent, _, _ in missing:
        target = layout[peer]
        base, offsets = target['workspace'], target['offset_map']
        entries = []
        for chunk, (start, end) in enumerate(intervals):
            if stage == 'gather':
                entries.extend(('payload', token, base + offsets['gather'] + (sender % 8 * tokens + token) * 512)
                               for token in range(start, end))
                entries.append(('SO', chunk, base + offsets['gather_ready'] + (chunk * 8 + sender % 8) * 512))
            elif stage == 'local_return':
                if peer % 8 == (sender % 8 + 1) % 8:
                    entries.extend(('payload', token, base + offsets['returned'] + (token * 8 + sender // 8) * 512)
                                   for token in range(start, end) if token % 2)
                if chunk in steps:
                    entries.append(('SO', chunk, base + offsets['local_ready'] + sender % 8 * 512 + steps.index(chunk) * 8))
            else:
                lane = physical // 20
                owner = (peer - sender) % 8 if mode == 'direct' else legacy_owner(sender, peer, chunk)
                if owner != physical % 20:
                    continue
                if peer == sender ^ 8:
                    entries.extend(('payload', token, base + offsets['returned'] + (token * 8 + sender // 8) * 512)
                                   for token in range(start, end) if (token % 4 == 3) == bool(lane))
                if chunk in cross_signal_chunks(sender, peer, tokens, mode):
                    entries.append(('SO', chunk, base + offsets['completed'] + ((sender * len(ends) + chunk) * 2 + lane) * 512))
        inspected = [entries[slot - origin] for slot in absent[:4]]
        addresses = {entry[2] for entry in inspected}
        observed = set()
        file = case / 'OUTPUT/stat_trace' / f'Host{peer}_ba_layer_trace.json'
        if file.is_file():
            with file.open('rb') as stream:
                raw = stream.read(trace_limit(tokens, mode))
                truncated = bool(stream.read(1))
            for match in TRACE_ARGS.finditer(raw):
                if b'"WR_DATA"' not in match[0]:
                    continue
                args = json.loads(match[0])
                address = args['oriAddr_']
                address = int(address, 0) if isinstance(address, str) else int(address)
                if address in addresses:
                    observed.add(address)
        else:
            truncated = True
        result.append([sender, peer, physical, stage, truncated,
                       [[kind, index, address in observed] for kind, index, address in inspected]])
    return result


def trace_limit(tokens, mode):
    return MAX_TRACE_BYTES * (8 if tokens == 1024 or mode == 'physical' else 1)


def direct_activity(case):
    layouts = json.loads((case / 'fixture-data/trace-offsets.json').read_text())
    totals, samples = Counter(), []
    for rank, layout in enumerate(layouts):
        bindings = layout['sq_bindings']
        local_sq = set(bindings['local_return'])
        gather_cq = {address - 0x1900000000 + CQ_BASE for address in bindings['gather']}
        path = case / 'OUTPUT/stat_trace' / f'Host{rank}_ba_layer_trace.json'
        with path.open('rb') as stream:
            raw = stream.read(trace_limit(32, None))
            if stream.read(1):
                totals['truncated'] += 1
                continue
        trace = json.loads(raw)
        events = trace['traceEvents'] if isinstance(trace, dict) else trace
        first_return, last_gather = None, None
        for event in events:
            args, timestamp = event.get('args', {}), event.get('ts')
            kind, address = args.get('busPayloadType_'), args.get('oriAddr_')
            if timestamp is None or address is None or kind not in ('WR_REQ', 'RD_REQ'):
                continue
            address = int(address, 0) if isinstance(address, str) else int(address)
            rank_base = rank * GM_BYTES
            if not rank_base <= address < rank_base + GM_BYTES:
                continue
            tag = rank_base + (address - rank_base) // RING_BYTES * RING_BYTES
            if kind == 'RD_REQ' and tag in local_sq:
                first_return = timestamp if first_return is None else min(first_return, timestamp)
            if kind == 'WR_REQ' and tag in gather_cq:
                last_gather = timestamp if last_gather is None else max(last_gather, timestamp)
        if first_return is not None and last_gather is not None:
            totals['ranks_with_both'] += 1
            totals['ranks_return_fetch_before_last_gather_cq'] += first_return < last_gather
            if len(samples) < 4:
                samples.append([rank, first_return, last_gather])
    return dict(totals=totals, columns=['rank', 'first_local_return_sq_fetch_ts', 'last_gather_cq_write_ts'], samples=samples)
