# Copyright (c) 2026 TileXR Project.
"""Bind native Combine-HT capture and export its complete SST profiling streams."""
import csv
import hashlib
import json
from pathlib import Path
import struct
import sys

CAPACITY = 32768
STATE_STRIDE = 16
ADDRESS = 0x430000000
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


def bind(case, lanes, timing_only=False):
    workload_path = case / 'workload.yml'
    export_path = case / 'export-layout.json'
    workload = json.loads(workload_path.read_text())
    exports = json.loads(export_path.read_text())
    for row in exports['ranks']:
        chip = workload['workload_config'][f"chip{row['rank']}"]
        assert chip['block_dim'] == lanes
        address = row['sys_va_base_addr'] + ADDRESS
        folder = case / 'input_bin' / f"rank{row['rank']:02}"
        profile = folder / 'profile.bin'
        image = profile_image(address, lanes, 16 if timing_only else CAPACITY)
        end = address + len(image)
        assert end <= row['sys_va_base_addr'] + chip['gm_size']
        assert all(end <= segment['addr'] or address >= segment['addr'] + segment['size']
                   for segment in (*row['exports'].values(), *row['guards']))
        profile.write_bytes(image)
        arguments = folder / 'kernel_args.bin'
        data = bytearray(arguments.read_bytes())
        assert len(data) == 296 and struct.unpack_from('<Q', data, 80)[0] == 0
        values = chip['kernel_param']
        if timing_only:
            data.extend(struct.pack('<Q', address))
            for key, value in values.items():
                if key.startswith('load_'):
                    value['param_offset'] = str(int(value['param_offset']) + 8)
            values['sstProfile'] = dict(value=address, param_offset='296', param_type='11')
        else:
            struct.pack_into('<Q', data, 80, address)
            values['word10']['value'] = address
        arguments.write_bytes(data)
        for key, value in values.items():
            if key.startswith('load_'):
                limit = value['value'] + Path(value['input_bin']).stat().st_size
                assert end <= value['value'] or address >= limit, (row['rank'], key)
        offset = max(int(value['param_offset']) for value in values.values()) + 8
        values['load_profile'] = dict(value=address, param_offset=str(offset),
                                      param_type='11', input_bin=str(profile))
        chip['bin_param']['size'] = max(chip['bin_param']['size'], offset + 8)
        region = dict(addr=address, size=len(image))
        chip['output_param']['profile'] = region
        row['exports']['profile'] = region
    workload_path.write_text(json.dumps(workload, indent=2) + '\n')
    export_path.write_text(json.dumps(exports, indent=2) + '\n')


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
    assert 0 < lanes <= 8 and 0 < capacity <= stride <= CAPACITY and state_stride == STATE_STRIDE
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


def summarize(case, ranks, lanes, send_rows, timing_only=False):
    sys.path.insert(0, str(SOURCE / 'tools/profiling/python'))
    sys.path.insert(0, str(SOURCE / 'tests/moonep_combine_ht'))
    from intra_send_profile import decode_stream, export, read_sst_streams

    clock = json.loads(Path(__file__).with_name('clock_evidence.json').read_text())
    config = case / 'v310_config/davidV100_simulator_config.yml'
    assert hashlib.sha256(config.read_bytes()).hexdigest() == clock['config_sha256']
    assert len(clock['paired_samples']) == 26
    assert all(row['sys_counter'] == row['popped_core_tick'] for row in clock['paired_samples'])
    cycles_per_us = clock['cycles_per_us']
    assert cycles_per_us == clock['frequency_hz'] / 1e6
    submissions = []
    intervals = []
    layout = json.loads((case / 'export-layout.json').read_text())
    for row in layout['ranks']:
        rank = row['rank']
        records = case / 'actual' / f'rank{rank:02}' / 'profile.bin'
        streams = list(read_sst_streams(records, row['exports']['profile']['addr'], rank))
        assert len(streams) == lanes
        for lane, stream in enumerate(streams):
            assert stream.complete and stream.dropped_count == 0, ('profiling_lane', rank, lane)
            begin = [record.timestamp for record in stream.records if record.event_id == 1]
            end = [record.timestamp for record in stream.records if record.event_id == 2]
            assert len(begin) == len(end) == 1 and 0 < begin[0] < end[0], ('profiling_markers', rank, lane)
            intervals.append(dict(rank=rank, lane=lane, begin=begin[0], end=end[0], elapsed_ticks=end[0] - begin[0]))
            if timing_only:
                assert len(stream.records) == 2
            else:
                submissions.extend(decode_stream(stream))
    assert len(intervals) == ranks * lanes
    if timing_only:
        out = case / 'profiling'
        out.mkdir()
        origin = min(row['begin'] for row in intervals)
        events = [{'ph': 'X', 'name': 'Combine-HT local send', 'pid': row['rank'], 'tid': row['lane'],
                   'ts': (row['begin'] - origin) / cycles_per_us, 'dur': row['elapsed_ticks'] / cycles_per_us}
                  for row in intervals]
        (out / 'intra_send_perfetto.json').write_text(json.dumps({'traceEvents': events}))
        with (out / 'operator_intervals.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=intervals[0].keys())
            writer.writeheader()
            writer.writerows(intervals)
    else:
        assert sum(sent.wqes for sent in submissions if not sent.signal) == send_rows
        export(submissions, case / 'profiling', cycles_per_us)
    (case / 'profiling-records.json').write_text(json.dumps(intervals, indent=2) + '\n')
    peak = max(row['elapsed_ticks'] for row in intervals)
    summary = dict(profiling_operator_us=peak / cycles_per_us, profiling_max_lane_ticks=peak,
                   profiling_lanes=len(intervals), profiling_dropped_records=0,
                   profiling_clock_receipt=clock['receipt_event_id'],
                   profiling_detail='operator_intervals' if timing_only else 'sq_cq')
    if not timing_only:
        summary.update(profiling_cqes_unobserved=sum(sent.cq_requests - len(sent.observed) for sent in submissions),
                       profiling_batches=len(submissions))
    (case / 'profiling-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary
