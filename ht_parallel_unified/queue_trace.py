"""Count retained BS32 CQ writes by route on a fixed sample of ranks."""
from collections import defaultdict
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
