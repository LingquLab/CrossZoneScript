# Copyright (c) 2026 TileXR Project.
"""Report queue and lane progress at a fixed simulated time."""
from collections import Counter
import struct

from addressed_exports import reconstruct_die_output
from runtime import BASES, BB_BYTES, PHYSICAL_QPS, WORLD

QUEUE_STATE_BYTES = WORLD * PHYSICAL_QPS * BB_BYTES
QUEUES = ('count', 'sq_head', 'sq_tail', 'cq_head', 'cq_tail')


def add_queue_exports(workload):
    for chip in workload['workload_config'].values():
        chip['output_param']['qstate'] = {
            'addr': chip['sys_va_base_addr'] + BASES['queue_state'],
            'size': len(QUEUES) * QUEUE_STATE_BYTES,
        }


def summarize(case, layout, decoder):
    def read(rank, field, address, size):
        paths = [case / 'bin' / f'david_{rank}_die{die}_{field}.bin' for die in (1, 2)]
        return reconstruct_die_output(paths, address, size, decoder)

    statuses = Counter()
    pending = []
    incomplete = []
    missing = {'status': [], 'qstate': []}
    for row in layout['ranks']:
        rank = row['rank']
        status = row['exports']['status']
        try:
            values = read(rank, 'status', status['addr'] - row['sys_va_base_addr'], status['size'])
        except (FileNotFoundError, ValueError):
            missing['status'].append(rank)
        else:
            for lane in range(16):
                value = struct.unpack_from('<I', values, lane * 32)[0]
                statuses[value] += 1
                if value != 0:
                    incomplete.append((rank, lane, value))

        try:
            state = read(rank, 'qstate', BASES['queue_state'], len(QUEUES) * QUEUE_STATE_BYTES)
        except (FileNotFoundError, ValueError):
            missing['qstate'].append(rank)
            continue
        for index in range(WORLD * PHYSICAL_QPS):
            counters = {
                name: struct.unpack_from('<I', state, plane * QUEUE_STATE_BYTES + index * BB_BYTES)[0]
                for plane, name in enumerate(QUEUES)
            }
            if counters['sq_head'] != counters['sq_tail']:
                peer, slot = divmod(index, PHYSICAL_QPS)
                pending.append(dict(rank=rank, peer=peer, physical_slot=slot, **counters))

    return dict(lane_status_histogram=dict(statuses), incomplete_lanes=incomplete[:32],
                incomplete_lane_count=len(incomplete), pending_queues=pending[:32],
                pending_queue_count=len(pending), missing_exports=missing)
