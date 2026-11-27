# Copyright (c) 2026 TileXR Project.
"""Report queue and lane progress at a fixed simulated time."""
from collections import Counter
import json
from pathlib import Path
import struct
import sys

if __name__ == '__main__':
    sys.path.insert(0, '/work/source/tools/unitest/simulator/experiments/inter_server_send/sst')

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

    completion = {}
    missing['completed'] = []
    for row in layout['ranks']:
        rank = row['rank']
        region = row['exports']['completed']
        try:
            completion[rank] = read(rank, 'completed', region['addr'] - row['sys_va_base_addr'], region['size'])
        except (FileNotFoundError, ValueError):
            missing['completed'].append(rank)

    visibility = Counter()
    absent = []
    for queue in pending:
        receiver = completion.get(queue['peer'])
        if receiver is None:
            visibility['unknown'] += 1
            continue
        lane = queue['physical_slot'] // 20
        # The test uses four chunks and one notification per lane at the last chunk.
        slot = 8 + (queue['rank'] * 4 + 3) * 2 + lane
        value = struct.unpack_from('<q', receiver, slot * 512)[0]
        visibility['visible' if value == 2026 else 'absent'] += 1
        if value != 2026:
            absent.append(queue | {'destination_completion': value})

    return dict(lane_status_histogram=dict(statuses), incomplete_lanes=incomplete[:32],
                incomplete_lane_count=len(incomplete), pending_queues=pending[:32],
                pending_queue_count=len(pending), missing_exports=missing,
                pending_notification_visibility=dict(visibility), absent_notification_samples=absent[:32])


if __name__ == '__main__':
    case = Path('/work/cases/t64-inter-server-send-a3')
    result = json.loads((case / 'phase-result.json').read_text())
    assert result['status'] == 'diagnostic_snapshot' and result['stop_at_us'] == 155
    assert result['kernel_sha256'] == '42ede4b0f015dff5d3689a0576d49c959d5b5417912c9c6a5bad0aac5ab98463'
    assert result['routes_sha256'] == '5cf3a46d45e57912b6156c431069867bacc803c803307b288a043a33c3bc7c49'
    layout = json.loads((case / 'export-layout.json').read_text())
    decoder = json.loads((case / 'sst-environment.json').read_text())['decoder_library']
    print(json.dumps(summarize(case, layout, Path(decoder)), separators=(',', ':')))
