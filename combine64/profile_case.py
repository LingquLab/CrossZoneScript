# Copyright (c) 2026 TileXR Project.
"""Bind native Combine-HT capture and export its complete SST profiling streams."""
import hashlib
import json
from pathlib import Path
import struct
import sys

CAPACITY = 32768
STATE_STRIDE = 16
ADDRESS = 0x430000000
SOURCE = Path('/work/source')


def profile_image(address, lanes):
    timestamps = 512
    events = timestamps + lanes * CAPACITY * 8
    counts = events + lanes * CAPACITY * 4
    dropped = counts + lanes * STATE_STRIDE * 4
    states = dropped + lanes * STATE_STRIDE * 4
    image = bytearray(states + lanes * STATE_STRIDE * 4)
    struct.pack_into('<8I7Q', image, 0, 2, 88, 1, lanes, CAPACITY, CAPACITY, STATE_STRIDE, 0,
                     address + timestamps, address + events, address + counts, address + dropped,
                     address + states, 0, 1)
    return image


def bind(case, lanes):
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
        image = profile_image(address, lanes)
        end = address + len(image)
        assert end <= row['sys_va_base_addr'] + chip['gm_size']
        assert all(end <= segment['addr'] or address >= segment['addr'] + segment['size']
                   for segment in (*row['exports'].values(), *row['guards']))
        profile.write_bytes(image)
        arguments = folder / 'kernel_args.bin'
        data = bytearray(arguments.read_bytes())
        assert len(data) == 296 and struct.unpack_from('<Q', data, 80)[0] == 0
        struct.pack_into('<Q', data, 80, address)
        arguments.write_bytes(data)
        values = chip['kernel_param']
        values['word10']['value'] = address
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


def summarize(case, ranks, lanes, send_rows):
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
            submissions.extend(decode_stream(stream))
    assert len(intervals) == ranks * lanes
    assert sum(sent.wqes for sent in submissions if not sent.signal) == send_rows
    missing = sum(sent.cq_requests - len(sent.observed) for sent in submissions)
    export(submissions, case / 'profiling', cycles_per_us)
    (case / 'profiling-records.json').write_text(json.dumps(intervals, indent=2) + '\n')
    peak = max(row['elapsed_ticks'] for row in intervals)
    summary = dict(profiling_operator_us=peak / cycles_per_us, profiling_max_lane_ticks=peak,
                   profiling_lanes=len(intervals), profiling_dropped_records=0,
                   profiling_clock_receipt=clock['receipt_event_id'],
                   profiling_cqes_unobserved=missing, profiling_batches=len(submissions))
    (case / 'profiling-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary
