# Copyright (c) 2026 TileXR Project.
"""Bind each SST timing wrapper to its own DirectWriter buffer and check all AIV lanes."""
import hashlib
import json
from pathlib import Path
import struct

CAPACITY = 16
STATE_STRIDE = 16
ADDRESS = 0x430000000
CLOCK = Path(__file__).with_name('clock_evidence.json')


def layout(lanes):
    timestamps = 512
    ids = timestamps + lanes * CAPACITY * 8
    counts = ids + lanes * CAPACITY * 4
    dropped = counts + lanes * STATE_STRIDE * 4
    states = dropped + lanes * STATE_STRIDE * 4
    return timestamps, ids, counts, dropped, states, states + lanes * STATE_STRIDE * 4


def profile_image(address, lanes):
    timestamps, ids, counts, dropped, states, size = layout(lanes)
    image = bytearray(size)
    struct.pack_into('<8I7Q', image, 0, 2, 88, 1, lanes, CAPACITY, CAPACITY, STATE_STRIDE, 0,
                     address + timestamps, address + ids, address + counts, address + dropped,
                     address + states, 0, 1)
    return image


def bind(case, lanes):
    workload_path = case / 'workload.yml'
    export_path = case / 'export-layout.json'
    workload = json.loads(workload_path.read_text())
    exports = json.loads(export_path.read_text())
    for row in exports['ranks']:
        rank = row['rank']
        chip = workload['workload_config'][f'chip{rank}']
        assert chip['block_dim'] == lanes
        base = row['sys_va_base_addr']
        address = base + ADDRESS
        folder = case / 'input_bin' / f'rank{rank:02}'
        profile = folder / 'profile.bin'
        image = profile_image(address, lanes)
        assert address + len(image) <= base + chip['gm_size']
        assert all(address + len(image) <= segment['addr'] or address >= segment['addr'] + segment['size']
                   for segment in (*row['exports'].values(), *row['guards']))
        profile.write_bytes(image)
        arguments = folder / 'kernel_args.bin'
        offset = arguments.stat().st_size
        assert offset in (200, 208, 248, 296, 568)
        with arguments.open('ab') as stream:
            stream.write(struct.pack('<Q', address))
        values = chip['kernel_param']
        for key, value in values.items():
            if key.startswith('load_'):
                data_end = value['value'] + Path(value['input_bin']).stat().st_size
                assert address + len(image) <= value['value'] or address >= data_end, (rank, key)
                value['param_offset'] = str(int(value['param_offset']) + 8)
        assert 'sstProfile' not in values and 'load_profile' not in values
        load_offset = max(int(value['param_offset']) for value in values.values()) + 8
        values['sstProfile'] = dict(value=address, param_offset=str(offset), param_type='11')
        values['load_profile'] = dict(value=address, param_offset=str(load_offset),
                                      param_type='11', input_bin=str(profile))
        chip['bin_param']['size'] = max(chip['bin_param']['size'], load_offset + 8)
        region = dict(addr=address, size=len(image))
        chip['output_param']['profile'] = region
        row['exports']['profile'] = region
    workload_path.write_text(json.dumps(workload, indent=2) + '\n')
    export_path.write_text(json.dumps(exports, indent=2) + '\n')


def read_intervals(raw, lanes, rank):
    timestamps, ids, counts, dropped, states, size = layout(lanes)
    assert len(raw) == size
    for lane in range(lanes):
        state_slot = lane * STATE_STRIDE * 4
        n, = struct.unpack_from('<I', raw, counts + state_slot)
        lost, = struct.unpack_from('<I', raw, dropped + state_slot)
        state, = struct.unpack_from('<I', raw, states + state_slot)
        assert (n, lost, state) == (2, 0, 2), ('profiling_lane', rank, lane, n, lost, state)
        begin, end = struct.unpack_from('<2Q', raw, timestamps + lane * CAPACITY * 8)
        begin_id, end_id = struct.unpack_from('<2I', raw, ids + lane * CAPACITY * 4)
        assert (begin_id, end_id) == (1, 2) and 0 < begin < end, ('profiling_markers', rank, lane, begin, end)
        yield dict(rank=rank, lane=lane, begin=begin, end=end, elapsed_ticks=end - begin)


def summarize(case, ranks, lanes):
    clock = json.loads(CLOCK.read_text())
    config = case / 'v310_config/davidV100_simulator_config.yml'
    assert hashlib.sha256(config.read_bytes()).hexdigest() == clock['config_sha256']
    assert len(clock['paired_samples']) == 26
    assert all(row['sys_counter'] == row['popped_core_tick'] for row in clock['paired_samples'])
    assert clock['cycles_per_us'] == clock['frequency_hz'] / 1e6
    intervals = []
    for rank in range(ranks):
        raw = (case / 'actual' / f'rank{rank:02}' / 'profile.bin').read_bytes()
        intervals.extend(read_intervals(raw, lanes, rank))
    (case / 'profiling-records.json').write_text(json.dumps(intervals, indent=2) + '\n')
    peak = max(row['elapsed_ticks'] for row in intervals)
    summary = dict(profiling_operator_us=peak / clock['cycles_per_us'], profiling_max_lane_ticks=peak,
                   profiling_lanes=len(intervals), profiling_dropped_records=0,
                   profiling_clock_receipt=clock['receipt_event_id'])
    (case / 'profiling-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary
