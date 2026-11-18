"""读取固定样本集合，避免将调参和预热数据混入三次交错复测的统计。"""
import json
import math
from pathlib import Path
import statistics

root, revision, groups = Path(ROOT_VALUE), REV_VALUE, GROUPS_VALUE
arms = ('original', 'sync', 'no-gate', 'local-cross')


def stats(values):
    return dict(n=len(values), min=min(values), median=statistics.median(values), max=max(values))


def read(label, group, warmup):
    folder = root/'cases'/label
    row = json.loads((folder/'summary.json').read_text())
    cfg = json.loads((folder/'config.json').read_text())
    assert row['runtime_commit'] == revision and row['trace'] == 'perf'
    assert row['bytes_per_peer'] == group['peer_bytes'] and row['ranks'] == row['block_dim'] == 64
    assert row['total_bytes_per_rank'] == 64*group['peer_bytes']
    assert cfg['iterations'] == 1 and cfg['rank_size'] == 64
    phase = row['schedule_profile']
    if row['mode'] == 'local-cross':
        assert phase['width'] == group['clock']['width']
        targets = group['clock']['targets']
        expected = targets + [targets[-1]]*(4-len(targets))
        assert cfg['schedule']['targets'] == phase['targets'] == expected
        assert cfg['schedule']['model_source'] == 'sst_identity'
        assert cfg['schedule']['delay_cycles'] == [0]*64
    assert row['mode'] in arms
    if phase:
        assert phase['records'] == 4096 and not phase['profile']
        assert phase['gated_records'] == (2048 if row['mode'] == 'local-cross' else 0)
    elapsed = row['simulated_us']
    assert isinstance(elapsed, (float, int)) and math.isfinite(elapsed) and elapsed > 0
    return dict(label=label, peer_bytes=group['peer_bytes'], mode=row['mode'], warmup=warmup,
                simulated_us=elapsed, payload_valid=row['validated'], cleanup=row['remaining_processes'] == [],
                misses=phase['scheduling_misses'] if phase else 0,
                scheduling_errors=phase['scheduling_errors'] if phase else 0,
                gated_records=phase['gated_records'] if phase else 0)


calls, tables = [], []
for group in groups:
    warmup = [read(label, group, True) for label in group['warmup']]
    assert {r['mode'] for r in warmup} == set(arms)
    calls.extend(warmup)
    rounds = [[read(label, group, False) for label in labels] for labels in group['rounds']]
    assert all({r['mode'] for r in rows} == set(arms) for rows in rounds)
    measured = [row for rows in rounds for row in rows]
    calls.extend(measured)
    medians = {arm: statistics.median(r['simulated_us'] for r in measured if r['mode'] == arm) for arm in arms}
    paired = [{r['mode']:r for r in rows} for rows in rounds]
    for arm in arms:
        rows = [r for r in measured if r['mode'] == arm]
        valid_times = [r['simulated_us'] for r in rows if r['payload_valid']]
        on_time = [r['simulated_us'] for r in rows if r['payload_valid'] and not r['misses'] and not r['scheduling_errors']]
        median = medians[arm]
        gated = sum(r['gated_records'] for r in rows)
        tables.append(dict(peer_bytes=group['peer_bytes'], mode=arm, all_calls_us=stats([r['simulated_us'] for r in rows]),
            payload_passed=sum(r['payload_valid'] for r in rows), payload_passed_us=stats(valid_times) if valid_times else None,
            on_time_clock_us=stats(on_time) if gated and on_time else None,
            misses=sum(r['misses'] for r in rows), scheduling_errors=sum(r['scheduling_errors'] for r in rows), gated_records=gated,
            miss_rate=sum(r['misses'] for r in rows)/gated if gated else None,
            error_rate=sum(r['scheduling_errors'] for r in rows)/gated if gated else None,
            latency_reduction_vs_original_pct=(1-median/medians['original'])*100,
            latency_reduction_vs_sync_pct=(1-median/medians['sync'])*100,
            paired_latency_reduction_vs_sync_pct=stats([(1-r[arm]['simulated_us']/r['sync']['simulated_us'])*100 for r in paired]),
            logical_payload_GBps=64*group['peer_bytes']/(median*1000)))

matrix = [p for p in (root/'cases').iterdir() if p.is_dir()]
saved = [json.loads((p/'summary.json').read_text()) for p in matrix if (p/'summary.json').exists()]
print(json.dumps(dict(read_only=True, simulation_launched=False, runtime_commit=revision,
    matrix_cases=len(matrix), completed_cases=len(saved), payload_passed_cases=sum(r['validated'] for r in saved),
    incomplete_labels=[p.name for p in matrix if not (p/'summary.json').exists()],
    warmup_calls=12, measured_calls=36, final_payload_passed=sum(r['payload_valid'] for r in calls),
    final_cleanup_passed=all(r['cleanup'] for r in calls), physical_clock_calibration_verified=False,
    tables=tables, calls=calls)))
