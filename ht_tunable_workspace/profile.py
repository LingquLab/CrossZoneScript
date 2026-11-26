"""Prepare and read the pinned TileXR DirectWriter DeviceView ABI 2."""
from collections import Counter
import json
import struct

PROFILE_ADDRESS = 0x40A000000
PROFILE_CAPACITY = 4096
PROFILE_STATE_STRIDE = 16
FINAL_BASE = 2048
FINAL_STRIDE = 4
SEND_BASE = 4096
SEND_STRIDE = 2


def profile_binding(address, blocks):
    timestamps = 128
    events = timestamps + blocks * PROFILE_CAPACITY * 8
    counts = events + blocks * PROFILE_CAPACITY * 4
    drops = counts + blocks * PROFILE_STATE_STRIDE * 4
    states = drops + blocks * PROFILE_STATE_STRIDE * 4
    size = states + blocks * PROFILE_STATE_STRIDE * 4
    descriptor = struct.pack('<8I7Q', 2, 88, 1, blocks, PROFILE_CAPACITY, PROFILE_CAPACITY,
                             PROFILE_STATE_STRIDE, 0, address + timestamps, address + events,
                             address + counts, address + drops, address + states, 0, 1)
    return descriptor.ljust(size, b'\0')


def extract_pipeline_profile(case, blocks, send_start, send_cores, step_ends, chunks, directory='actual'):
    steps = len(step_ends)
    final_start = blocks - 16
    compute = {step: [] for step in range(steps)}
    all_events = []
    sample = None
    stream_states = Counter()
    overlap = Counter()
    for rank in range(64):
        path = case / directory / f'rank{rank:02d}' / 'profile.bin'
        raw = path.read_bytes()
        view = struct.unpack_from('<8I7Q', raw)
        assert view[:8] == (2, 88, 1, blocks, PROFILE_CAPACITY, PROFILE_CAPACITY, PROFILE_STATE_STRIDE, 0)
        base = view[8] - 128
        timestamps, events, counts, drops, states = (address - base for address in view[8:13])
        per_aiv = {}
        for aiv in range(blocks):
            state_offset = aiv * PROFILE_STATE_STRIDE * 4
            state = struct.unpack_from('<I', raw, states + state_offset)[0]
            stream_states[state] += 1
            if state == 0:
                continue
            count = struct.unpack_from('<I', raw, counts + state_offset)[0]
            dropped = struct.unpack_from('<I', raw, drops + state_offset)[0]
            assert state == 2 and dropped == 0 and 0 < count <= PROFILE_CAPACITY, (rank, aiv, state, count, dropped)
            record = aiv * PROFILE_CAPACITY
            ids = struct.unpack_from(f'<{count}I', raw, events + record * 4)
            times = struct.unpack_from(f'<{count}Q', raw, timestamps + record * 8)
            lane = sorted(zip(times, ids))
            per_aiv[aiv] = lane
            all_events.append([rank, aiv, lane])
        ready_later_id = SEND_BASE + (chunks - 1) * SEND_STRIDE
        ready_later = [cycle for aiv in range(send_start, send_start + send_cores)
                       for cycle, event in per_aiv[aiv] if event == ready_later_id]
        assert len(ready_later) == send_cores
        submitted_later = [cycle for aiv in range(send_start, send_start + send_cores)
                           for cycle, event in per_aiv[aiv] if event == ready_later_id + 1]
        assert submitted_later
        first_compute = []
        for aiv in range(final_start, blocks):
            lane = per_aiv[aiv]
            final_events = [(cycle, event) for cycle, event in lane if FINAL_BASE <= event < FINAL_BASE + steps * FINAL_STRIDE]
            local = aiv - final_start
            widths = [right - left for left, right in zip([0] + step_ends, step_ends)]
            active = [step for step, width in enumerate(widths) if width * local // 16 < width * (local + 1) // 16]
            expected = [FINAL_BASE + step * FINAL_STRIDE + event for step in active for event in range(FINAL_STRIDE)]
            assert [event for _, event in final_events] == expected, (rank, aiv)
            for index, step in enumerate(active):
                cycle = final_events[index * FINAL_STRIDE + 2][0]
                compute[step].append(cycle)
                if step == 0:
                    first_compute.append(cycle)
        overlap['ranks'] += 1
        overlap['compute_before_some_sender_last_chunk_ready'] += min(first_compute) < max(ready_later)
        overlap['compute_before_all_senders_last_chunk_ready'] += min(first_compute) < min(ready_later)
        overlap['compute_before_last_chunk_last_send_submission'] += min(first_compute) < max(submitted_later)
        if rank == 0:
            sample = dict(rank=0, first_final_compute=min(first_compute), first_last_chunk_ready=min(ready_later),
                          last_last_chunk_ready=max(ready_later), last_send_submission=max(submitted_later))
    result = dict(expected_steps=steps, streams=stream_states, same_rank_comparison=overlap, sample_cycles=sample,
                  first_compute_cycle_by_step=[min(values) for values in compute.values()],
                  last_compute_cycle_by_step=[max(values) for values in compute.values()])
    (case / 'tunable-profile-markers.json').write_text(json.dumps(all_events, separators=(',', ':')))
    (case / 'tunable-profile-summary.json').write_text(json.dumps(result, separators=(',', ':')))
    return result


def decode_sparse_profile(dump_dir, rank, region, blocks):
    address, size = region['addr'], region['size']
    assert address % 512 == 0
    image = bytearray(size)
    sectors = set()
    for die in (0, 1):
        path = dump_dir / f'david_{rank}_die{die + 1}_profile.bin'
        with path.open('rb') as stream:
            while record := stream.read(520):
                assert len(record) == 520, (rank, die, 'truncated profile sector')
                sector, = struct.unpack_from('<Q', record)
                owner = ((sector >> 9) ^ (sector >> 16) ^ (sector >> 23)) & 1
                if owner != die or not address <= sector < address + size:
                    continue
                offset = sector - address
                end = min(offset + 512, size)
                value = record[8:8 + end - offset]
                assert offset not in sectors or image[offset:end] == value, (rank, die, offset)
                image[offset:end] = value
                sectors.add(offset)

    def require(offset, count):
        assert 0 <= offset <= offset + count <= size and all(
            sector in sectors for sector in range(offset // 512 * 512, offset + count, 512)
        ), (rank, 'missing recorded profile bytes', offset, count)

    require(0, 88)
    view = struct.unpack_from('<8I7Q', image)
    assert view[:8] == (2, 88, 1, blocks, PROFILE_CAPACITY, PROFILE_CAPACITY, PROFILE_STATE_STRIDE, 0)
    base = view[8] - 128
    assert base % (0x2000000000) == address
    assert image[:88] == profile_binding(base, blocks)[:88]
    timestamps, events, counts, drops, states = (pointer - base for pointer in view[8:13])
    for aiv in range(blocks):
        state_offset = aiv * PROFILE_STATE_STRIDE * 4
        for field in (counts, drops, states):
            require(field + state_offset, 4)
        state, = struct.unpack_from('<I', image, states + state_offset)
        count, = struct.unpack_from('<I', image, counts + state_offset)
        dropped, = struct.unpack_from('<I', image, drops + state_offset)
        assert state == 2 and dropped == 0 and 0 < count <= PROFILE_CAPACITY, (rank, aiv, state, count, dropped)
        first = aiv * PROFILE_CAPACITY
        require(timestamps + first * 8, count * 8)
        require(events + first * 4, count * 4)
    return image, len(sectors)
