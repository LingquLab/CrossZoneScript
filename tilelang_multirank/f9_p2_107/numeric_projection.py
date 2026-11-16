"""Project validated captures to numerical summaries; no device or network access."""
import hashlib
import json
import math
import statistics


def stats(values):
    assert values and all(type(v) in (int, float) and math.isfinite(v) for v in values)
    return {'n': len(values), 'mean': statistics.mean(values), 'median': statistics.median(values),
            'min': min(values), 'max': max(values)}


def mask(ids):
    assert len(ids) == len(set(ids)) and all(0 <= c < 64 for c in ids)
    return format(sum(1 << c for c in ids), '016x')


def project(groups):
    assert len(groups) == 10
    identities = []
    samples = {v: {r: [] for r in range(2)} for v in ('f9',)}
    capture_ids = set()
    for group, analysis in groups:
        assert group['status'] == analysis['status'] == 'PASS'
        assert group['tokens'] == 256 and group['mode'] == analysis['mode'] == 'capture'
        assert analysis['capture_checked'] and analysis['rounds'] == 1 and analysis['warmup'] == 20
        assert len(analysis['rank_results']) == 2 and len(analysis['pairs']) == 1
        identities.append({k: group[k] for k in ('group', 'variant', 'round', 'index', 'analysis_sha256',
                                                'summary_sha256', 'capture_evidence_sha256')})
        for rank, row in enumerate(analysis['rank_results']):
            assert row['rank'] == rank and row['cores'] == 64 and len(row['captures']) == 1
            cap = row['captures'][0]
            assert cap['capture_structure'] == cap['operator_correctness'] == 'PASS'
            assert cap['performance_sample_accepted'] and cap['unit'] == 'ticks' and cap['cycles_per_us'] is None
            assert cap['capture_id'] not in capture_ids and len(cap['lanes']) == 64
            capture_ids.add(cap['capture_id'])
            pair = analysis['pairs'][0]['ranks'][rank]
            assert pair['rank'] == rank
            values = {'msprof_dispatch_us': row['device_task_per_round_us'][0],
                      'acl_dispatch_us': row['acl_event_per_round_us'][0],
                      'pre_dispatch_event_us': pair['pre_allreduce']['pre_dispatch_event_us'],
                      'segment_event_us': pair['pre_allreduce']['segment_event_us']}
            samples[group['variant']][rank].append({'pair_round': group['round'], 'group': group['group'],
                'capture_id': cap['capture_id'], 'us': values, 'capture': cap})
    assert [g['index'] for g, _ in groups] == list(range(4, 14))
    ranks, tails = [], {}
    for variant, by_rank in samples.items():
        tails[variant] = []
        for rank, rows in by_rank.items():
            rows.sort(key=lambda r: r['pair_round'])
            assert [r['pair_round'] for r in rows] == list(range(1, 11))
            output = {'variant': variant, 'rank': rank,
                      'captures': [{k: r[k] for k in ('pair_round', 'group', 'capture_id', 'us')} for r in rows],
                      'us': {k: stats([r['us'][k] for r in rows]) for k in rows[0]['us']},
                      'tick_groups': {}, 'per_core': {}}
            for category in ('modules', 'functions', 'stages'):
                names = set(rows[0]['capture'][category])
                assert all(set(r['capture'][category]) == names for r in rows)
                output['tick_groups'][category] = {}
                for name in sorted(names):
                    values = [r['capture'][category][name] for r in rows]
                    output['tick_groups'][category][name] = {
                        'selected_stages': values[0]['selected_stages'],
                        'participating_core_masks_per_round': [mask(v['participating_core_ids']) for v in values],
                        'active_workload_core_masks_per_round': [mask(v['active_workload_core_ids']) for v in values],
                        'all_launch_core_normalized_ticks': stats([v['all_launch_core_normalized_ticks'] for v in values]),
                        'active_workload_mean_ticks_per_round': [v['active_workload_mean_ticks'] for v in values],
                        'core_mean_ticks': stats([v['mean_ticks'] for v in values]),
                        'core_max_ticks': stats([v['max_ticks'] for v in values]),
                        'participants_per_round': [v['participating_cores'] for v in values],
                        'workload_names': sorted(values[0]['workload']),
                        'workload_per_round': [[v['workload'][k] for k in sorted(values[0]['workload'])] for v in values]}
            # Column arrays retain per-core participants and sum/max/n across independent rounds.
            # Absent conditional intervals have n=0 and null sum/max, never an invented zero duration.
            stage_names = ['F9.payload_clear']
            group_names = [(category, name) for category in ('modules', 'functions')
                           for name in sorted(rows[0]['capture'][category])]
            workload_names = sorted(rows[0]['capture']['lanes'][0]['workload'])
            output['per_core'] = {'stage_names': stage_names, 'workload_names': workload_names,
                                 'group_names': group_names, 'group_columns': ['n', 'sum_ticks', 'max_ticks'],
                                 'stage_columns': ['n', 'sum_ticks', 'max_ticks'],
                                 'workload_columns': ['sum', 'min', 'max'], 'cores': []}
            for core in range(64):
                lanes = [r['capture']['lanes'][core] for r in rows]
                assert all(lane['core'] == core for lane in lanes)
                stages = []
                for name in stage_names:
                    ticks = [lane['intervals'][name]['ticks'] for lane in lanes if name in lane['intervals']]
                    stages.append([len(ticks), sum(ticks) if ticks else None, max(ticks) if ticks else None])
                work = []
                for name in workload_names:
                    values = [lane['workload'][name] for lane in lanes]
                    work.append([sum(values), min(values), max(values)])
                groups_for_core = []
                for category, name in group_names:
                    ticks = []
                    for r in rows:
                        value = r['capture'][category][name]
                        totals = value['per_core_sum_ticks']
                        key = str(core) if str(core) in totals else core
                        if key in totals:
                            ticks.append(totals[key])
                    groups_for_core.append([len(ticks), sum(ticks) if ticks else None, max(ticks) if ticks else None])
                output['per_core']['cores'].append([core, stages, work, groups_for_core])
            ranks.append(output)
        for number in range(10):
            values = [by_rank[r][number] for r in range(2)]
            tails[variant].append({'pair_round': number + 1,
                'rank_max_us': {key: max(r['us'][key] for r in values) for key in values[0]['us']},
                'modules_max_ticks': {name: max(r['capture']['modules'][name]['max_ticks'] for r in values)
                                     for name in values[0]['capture']['modules']},
                'functions_max_ticks': {name: max(r['capture']['functions'][name]['max_ticks'] for r in values)
                                       for name in values[0]['capture']['functions']},
                'payload_clear_max_ticks': max(r['capture']['stages']['F9.payload_clear']['max_ticks'] for r in values)})
    return {'schema': 'tilelang_107_p2_f9_t256_numeric.v1', 'tokens_per_rank': 256, 'world_size': 2,
            'identity': identities, 'ranks': ranks, 'round_tails': tails,
            'clock': 'NOT_VERIFIED; durations only; no cross-core/rank timestamp subtraction',
            'units': 'ticks separate from ACL/msprof us; no conversion',
            'core_masks': '16 hexadecimal digits; bit c identifies core c',
            'per_core_row': '[core, payload_clear stages, workload, module/function groups]; mean=sum/n',
            'pre_dispatch_scope': 'AllReduce plus event boundaries; not a pure HCCL kernel duration',
            'segment_scope': 'same-stream event interval including waiting',
            'aggregation': 'equal ten rounds per variant/rank; core means use actual participants; per-core mean=sum/n',
            'tails_scope': 'max reported duration across ranks; not a globally aligned wall interval'}


def encode(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def chunks(value):
    raw = encode(value)
    parts = [raw[i:i + 16384] for i in range(0, len(raw), 16384)]
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'chunks': [{'index': i, 'bytes': len(part), 'sha256': hashlib.sha256(part).hexdigest(),
                        'text': part.decode('ascii')} for i, part in enumerate(parts)]}
