# Copyright (c) 2026 TileXR Project.
"""Bind native Combine-HT capture and export its complete SST profiling streams."""
import hashlib
import json
from pathlib import Path
import struct
import sys

CAPACITY = 32768
STATE_STRIDE = 16
SOURCE = Path('/work/source')


def profile_image(address, lanes, capacity):
    timestamps = 512
    events = timestamps + lanes * capacity * 8
    counts = events + lanes * capacity * 4
    dropped = counts + lanes * STATE_STRIDE * 4
    states = dropped + lanes * STATE_STRIDE * 4
    end = states + lanes * STATE_STRIDE * 4
    image = bytearray((end + 511) // 512 * 512)
    struct.pack_into('<8I7Q', image, 0, 2, 88, 1, lanes, capacity, capacity, STATE_STRIDE, 0,
                     address + timestamps, address + events, address + counts, address + dropped,
                     address + states, 0, 1)
    return image


def reconstruct_profile(paths, address, size, decoder):
    from addressed_exports import V310_DECODER_SHA256, digest, owning_die

    assert digest(decoder) == V310_DECODER_SHA256
    blocks = {}
    for die, path in enumerate(paths):
        with path.open('rb') as stream:
            while record := stream.read(520):
                assert len(record) == 520
                offset = int.from_bytes(record[:8], 'little')
                if owning_die(offset) != die:
                    continue
                if address < offset + 512 and offset < address + size:
                    assert blocks.setdefault(offset, record[8:]) == record[8:]
    image = bytearray(size)

    def copy(offset, size_to_copy):
        assert 0 <= offset <= len(image) and offset + size_to_copy <= len(image)
        end = offset + size_to_copy
        while offset < end:
            absolute = address + offset
            block = absolute // 512 * 512
            start = absolute - block
            count = min(end - offset, 512 - start)
            if block not in blocks:
                raise ValueError(f'missing owned profiling bytes at {absolute:#x}')
            image[offset:offset + count] = blocks[block][start:start + count]
            offset += count

    copy(0, 88)
    descriptor = struct.unpack_from('<8I7Q', image)
    version, descriptor_size, enabled, lanes, capacity, stride, state_stride, _ = descriptor[:8]
    assert (version, descriptor_size, enabled) == (2, 88, 1)
    assert 0 < lanes <= 64 and 0 < capacity <= stride <= CAPACITY and state_stride == STATE_STRIDE
    timestamps, events, counts, dropped, states = descriptor[8:13]
    base = descriptor[8] - 512
    assert base >= 0 and (base - address) % 0x2000000000 == 0
    for lane in range(lanes):
        state = lane * state_stride * 4
        for region in (counts, dropped, states):
            copy(region - base + state, 4)
        count = struct.unpack_from('<I', image, counts - base + state)[0]
        saved_drops = struct.unpack_from('<I', image, dropped - base + state)[0]
        completion = struct.unpack_from('<I', image, states - base + state)[0]
        assert 2 <= count <= capacity and saved_drops == 0 and completion == 2
        first = lane * stride
        copy(timestamps - base + first * 8, count * 8)
        copy(events - base + first * 4, count * 4)
    return bytes(image)


def summarize(case, rank_stats, cores):
    sys.path.insert(0, str(SOURCE / 'tools/profiling/python'))
    sys.path.insert(0, str(SOURCE / 'tests/moonep_combine_ht'))
    from intra_send_profile import read_sst_streams

    clock = json.loads(Path(__file__).with_name('clock_evidence.json').read_text())
    assert hashlib.sha256((case / 'v310_config/davidV100_simulator_config.yml').read_bytes()).hexdigest() == clock['config_sha256']
    assert len(clock['paired_samples']) == 26
    assert all(row['sys_counter'] == row['popped_core_tick'] for row in clock['paired_samples'])
    frequency = clock['cycles_per_us']
    assert frequency == clock['frequency_hz'] / 1e6
    layout = json.loads((case / 'export-layout.json').read_text())
    lanes = []
    for row, stats in zip(layout['ranks'], rank_stats):
        rank = row['rank']
        assert stats['rank'] == rank
        stream_file = case / f'actual/rank{rank:02}/profile.bin'
        streams = list(read_sst_streams(stream_file, row['exports']['profile']['addr'], rank))
        assert len(streams) == cores
        rank_reduced = rank_contributions = 0
        for lane, stream in enumerate(streams):
            assert stream.complete and stream.dropped_count == 0, (rank, lane, 'profile state')
            events = {}
            for marker in stream.records:
                events.setdefault(marker.event_id, []).append(marker.timestamp)
            assert events.keys() <= {1, 2, 3, 4, 5, 10, 11, 20, 21, 22, 23}
            assert len(events[1]) == len(events[2]) == 1 and 0 < events[1][0] <= events[2][0]
            if not stats['active']:
                assert events.keys() == {1, 2}
                continue
            stages = stats['chunks'] * stats['groups']
            assert all(len(events[key]) == stages for key in (3, 4, 5)), (rank, lane, 'stages')
            rank_reduced += len(events.get(10, []))
            rank_contributions += len(events.get(11, []))
            lanes.append((rank, lane, stats, events))
        assert rank_reduced == stats['reduced_tokens']
        assert rank_contributions == stats['contributions'] - stats['tokens']

    assert len(lanes) == sum(row['active'] for row in rank_stats) * cores
    origin = min(events[1][0] for _, _, _, events in lanes)
    traces, chunk_us = [], []
    peak = 0

    def duration(name, rank, lane, begin, end, **args):
        assert end >= begin >= origin
        return dict(ph='X', name=name, pid=rank, tid=lane, ts=(begin - origin) / frequency,
                    dur=(end - begin) / frequency, args=args)

    for rank, lane, stats, events in lanes:
        begin, end = events[1][0], events[2][0]
        peak = max(peak, end - begin)
        traces.append(duration('Combine-HT GatewayReduce', rank, lane, begin, end))
        for stage, (ready, done) in enumerate(zip(events[4], events[5])):
            chunk, group = divmod(stage, stats['groups'])
            traces.append(duration('Gateway stage', rank, lane, ready, done, chunk=chunk, group=group))
            if len(lanes) == 1:
                chunk_us.append((done - ready) / frequency)
        for ready, done in zip(events.get(22, []), events.get(23, [])):
            traces.append(duration('Token', rank, lane, ready, done))
        for tick in events.get(11, []):
            traces.append(dict(ph='i', s='t', name='Contribution', pid=rank, tid=lane,
                               ts=(tick - origin) / frequency))

    output = case / 'profiling'
    output.mkdir()
    trace = output / 'gateway_perfetto.json'
    trace.write_text(json.dumps({'traceEvents': traces}, separators=(',', ':')))
    result = dict(profiling_operator_us=peak / frequency, profiling_lanes=len(lanes),
                  profiling_dropped_records=0, profiling_clock_receipt=clock['receipt_event_id'],
                  trace_sha256=hashlib.sha256(trace.read_bytes()).hexdigest())
    if chunk_us:
        result.update(chunk_sample_us=chunk_us, mean_chunk_us=sum(chunk_us) / len(chunk_us))
    (case / 'profiling-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
