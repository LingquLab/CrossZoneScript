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


def detail_gather_queues(case):
    expected = set(range(36))
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
            missing_stage1 = tuple(sorted(set(range(9,18)) - cq))
            masks[missing_stage1] += 1
            counts['sq_head_write_records'] += evidence_row['sq_head']
            counts['doorbell_write_records'] += evidence_row['bells']
            counts['sq_fetch_starts'] += len(sq)
            counts['cq_slots'] += len(cq)
            counts['qps_missing_chunk1_so_cq'] += 17 not in cq
            counts['qps_with_chunk2_cq'] += any(18 <= slot < 27 for slot in cq)
            if missing_stage1 and len(examples) < 6:
                examples.append([rank, gather[ring], ring%40, sorted(sq), sorted(cq),
                                 evidence_row['sq_head'], evidence_row['bells']])
    return dict(summary=counts, most_common_missing_chunk1_cq_slots=masks.most_common(12),
                example_columns=['rank','peer','physical_slot','sq_fetch_start_bb','cq_bb',
                                 'sq_head_wr_req','doorbell_wr_req'], examples=examples)
