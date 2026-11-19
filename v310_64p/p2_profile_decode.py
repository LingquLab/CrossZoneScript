import json
import statistics
import struct

TIMERS = struct.Struct('<IHH6IQ56Q24x')
DETAILS = struct.Struct('<IHH4IQ60Q')
COARSE_NAMES = (
    'F1.route', 'dependency.role_barrier', 'F5.receive_counts', 'F6.prefix_layout',
    'F7.layout_gate', 'F8.receive_loop', 'F9.output_completion', 'F9.payload_clear',
)
EXTENDED_NAMES = (
    'runtime.setup_envelope', 'F2.count_construct', 'F3.count_publish',
    'F4.position_pack_send', 'F7.receive_plan', 'F9.drain_dependency',
)
MODULES = (('F0', 'F1'), ('F2', 'F3'), ('F4',), ('F5', 'F6'), ('F7', 'F8', 'F9'))


def decode_p2_records(case, mapping, outputs):
    records = []
    for rank in range(2):
        blobs = [outputs[rank, name].read_bytes() for name in ('profile_coarse', 'profile_functional')]
        assert all(len(blob) == 2048 for blob in blobs), 'PROFILE_READBACK_SIZE'
        for core in range(4):
            head = TIMERS.unpack_from(blobs[0], core * 512)
            extra = DETAILS.unpack_from(blobs[1], core * 512)
            assert head[:10] == (0x45504450, 5, 512, rank, core, 4, int(core == 3), 8, 16, 1), 'COARSE_HEADER'
            assert extra[:8] == (0x4550464e, 5, 512, rank, core, 4, 30, 1), 'FUNCTIONAL_HEADER'
            points, detail = head[10:50], head[50:]
            first, last = points[0], points[7]
            assert 0 < first < last and points[24] == 1 and points[23] == 2, 'PROFILE_COUNTER_DOMAIN'
            events = {}

            def add(name, pair):
                lo, hi = pair
                if lo == hi == 0:
                    return
                assert first <= lo <= hi <= last, (rank, core, name, pair)
                events[name] = pair

            for idx, name in enumerate(COARSE_NAMES):
                add(name, detail[2*idx:2*idx+2])
            for idx, name in enumerate(EXTENDED_NAMES):
                add(name, points[25+2*idx:27+2*idx])
            for event in mapping['events']:
                if 'slots' in event:
                    idx = int(event['slots'][0][1:])
                    add(event['name'], extra[8+idx:10+idx])
            for fn in ('F0', 'F1', 'F2', 'F3', 'F7', 'F8', 'F9'):
                assert any(n.startswith(fn+'.') for n in events), (rank, core, fn)
            assert ('F4.position_pack_send' in events) == (core != 3), 'F4_ROLE'
            for fn in ('F5.receive_counts', 'F6.prefix_layout'):
                assert (fn in events) == (core == 3), 'LAYOUT_ROLE'
            records.append({'rank': rank, 'core': core, 'first': first, 'last': last, 'events': events,
                            'issued_routes': points[9], 'count_records': points[10], 'receive_tokens': points[19]})
        rank_records = records[-4:]
        for key, expected in (('issued_routes', 8), ('count_records', 4), ('receive_tokens', 8)):
            assert sum(r[key] for r in rank_records) == expected, ('WORK_CONSERVATION', rank, key)

    def stats(lanes, names):
        values = []
        for lane in lanes:
            items = sorted(lane['events'][name] for name in names if name in lane['events'])
            assert all(left[1] <= right[0] for left, right in zip(items, items[1:])), 'INTERVAL_OVERLAP'
            if items:
                values.append((lane['core'], sum(end - start for start, end in items)))
        if not values:
            return {'cores': [], 'mean_ticks': None, 'max_ticks': None, 'slowest': []}
        peak = max(value for _, value in values)
        return {'cores': [core for core, _ in values], 'mean_ticks': statistics.mean(v for _, v in values),
                'max_ticks': peak, 'slowest': [core for core, value in values if value == peak]}

    groups = {f'F{i}': (f'F{i}',) for i in range(10)}
    groups.update({f'M{i}': fns for i, fns in enumerate(MODULES, 1)})
    totals = {}
    for rank in range(2):
        lanes = records[rank*4:(rank+1)*4]
        totals[str(rank)] = {
            label: stats(lanes, [name for fn in fns for name in mapping['function_total_parts'][fn]])
            for label, fns in groups.items()
        }
        totals[str(rank)]['substage'] = {name: stats(lanes, [name])
                                       for name in sorted({n for lane in lanes for n in lane['events']})}
    trace = []
    for lane in records:
        for name, (first, last) in lane['events'].items():
            trace.append({'name': name, 'ph': 'X', 'pid': lane['rank'], 'tid': lane['core'],
                          'ts': (first-lane['first'])/1000, 'dur': (last-first)/1000,
                          'args': {'start_tick': first, 'end_tick': last}})
    (case/'records-summary.json').write_text(json.dumps({'ranks': totals, 'records': records}, indent=2)+'\n')
    (case/'dispatch.raw-ticks.perfetto.json').write_text(json.dumps({
        'traceEvents': trace, 'metadata': {'clock_alignment': 'NOT_VERIFIED',
                                         'display_scale': '1 tick displayed as 1 ns'}})+'\n')
    return totals
