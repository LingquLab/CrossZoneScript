"""Keep tuning evidence separate from frozen comparisons and audit saved binary records."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import statistics
import subprocess
import sys

root, args = Path(COMPARE_ROOT_VALUE), COMPARE_ARGS_VALUE
revision, target = args['revision'], args['target']
assert json.loads((root/'build-summary.json').read_text())['runtime_commit'] == revision
sys.path.insert(0, str(root/'source/tools/unitest'))
from controller import direct_group_schedule as schedule

entry, peer, count = 32768, 65536, 5
work = root/'boundary-65536-gap-search-20261119'
assert work.is_dir(), 'screen the target first'
key = 'compare-t' + str(target)
receipt = work/(key + '.json')
assert not receipt.exists(), 'inspect existing comparison; never replay a frozen comparison'
screening = []
search_labels = []
for path in sorted(work.glob('t*.json')):
    evidence = path.read_bytes()
    data = json.loads(evidence)
    assert data['runtime_commit'] == revision and data['stage'] == 'candidate'
    assert data['status'] == 'completed' and len(data['calls']) == 3, 'resolve incomplete searches first'
    assert data['accepted'] == all(row['acceptance']['accepted'] for row in data['calls'])
    screening.append(dict(target=data['target'], sha256=hashlib.sha256(evidence).hexdigest(),
                          accepted=data['accepted'], body_budget=data['body_budget'],
                          simulated_us=data['simulated_us']))
    search_labels.extend(row['label'] for row in data['calls'])
selected = next(row for row in screening if row['target'] == target)
assert selected['accepted'], 'a rejected screening target cannot enter independent verification'
orders = [('local-cross', 'sync') if i % 2 else ('sync', 'local-cross') for i in range(1, count + 1)]
planned = [(mode, 'boundary-65536-' + key + '-' + mode + '-r' + str(i))
           for i, modes in enumerate(orders, 1) for mode in modes]
for _, label in planned:
    assert not (root/'cases'/label).exists()
    assert not (Path('/opt/sim/simulator/Testcases')/('schedule_' + revision[:8] + '_' + label)).exists()
    assert not (work/(label + '.runner.json')).exists()
case_source = COMPARE_CASE_SOURCE_VALUE
runner = root/('boundary-case-' + hashlib.sha256(case_source.encode()).hexdigest()[:12] + '.py')
assert runner.read_text() == case_source
state = dict(schema='tilexr.boundary.gap-comparison.v1', runtime_commit=revision,
             stage='independent-comparison', status='running', target=target, budget=target-entry,
             entry_target=entry, peer_bytes=peer, total_bytes_per_rank=peer*64,
             ranks=64, blocks_per_rank=64, width=32, trace='perf', samples_per_mode=count,
             limits=dict(epsilon=0, g_min=0, spread_max=299), screening_snapshot=screening,
             orders=orders, independent_verification=True, calls=[],
             selection_metric='median body_budget among boundary-valid screening targets',
             reference_basis='identity_assumption', physical_clock_calibration_verified=False,
             simulator_counter_frequency_verified=False, display_cycles_per_us=1650,
             conversion_source='user_provided')
with receipt.open('x') as stream:
    json.dump(state, stream, indent=2)


def save():
    receipt.write_text(json.dumps(state, indent=2))


def one(mode, label):
    phase_target = target if mode == 'local-cross' else 0
    spec = dict(mode=mode, trace='perf', peer_bytes=peer, label=label, width=32,
                entry_target=entry, targets=[phase_target], plan_id=2,
                max_wait_cycles=1000000, simulation_timeout=1800)
    capture = work/(label + '.runner.json')
    try:
        result = subprocess.run([sys.executable, str(runner), str(root), json.dumps(spec)],
                                capture_output=True, text=True, timeout=2000)
    except subprocess.TimeoutExpired as error:
        def decode(value):
            return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value
        capture.write_text(json.dumps(dict(status='timeout', stdout=decode(error.stdout),
                                           stderr=decode(error.stderr))))
        raise
    capture.write_text(json.dumps(dict(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)))
    assert result.returncode == 0 and not result.stderr, 'case runner failed; retain all evidence'
    row = json.loads(result.stdout)
    assert row['runtime_commit'] == revision and row['mode'] == mode and row['trace'] == 'perf'
    assert row['ranks'] == row['block_dim'] == 64 and row['bytes_per_peer'] == peer
    assert row['total_bytes_per_rank'] == peer*64
    assert row['validated'] and row['returncode'] == 0 and row['remaining_processes'] == []
    phase = row['schedule_profile']
    assert phase['version'] == 2 and phase['records'] == phase['entry_records'] == 4096
    assert phase['width'] == 32 and phase['entry_target'] == entry and phase['targets'] == [phase_target]*4
    assert phase['reference_basis'] == 'identity_assumption' and phase['payload_valid']
    assert phase['entry_on_time'] and phase['entry_status_counts']['on_time'] == 4096
    assert phase['scheduling_on_time'] and phase['boundary_timing_usable']
    assert phase['scheduling_misses'] == phase['scheduling_errors'] == 0
    assert type(row['simulated_us']) in (int, float) and row['simulated_us'] > 0
    assert phase['gap'] == phase['S2']-phase['C1'] and phase['spread'] == phase['L2']-phase['S2']
    assert phase['body_budget'] == phase['body_end']-entry
    acceptance = schedule.evaluate_boundary(phase, **state['limits'])
    assert acceptance['accepted'] == (phase['gap'] >= 0 and phase['spread'] <= 299)
    fields = ('C1', 'S2', 'L2', 'gap', 'spread', 'first_phase_budget', 'cross_ready_budget',
              'body_budget', 'body_end', 'limiting', 'boundary_failures')
    return dict(label=label, mode=mode, validated=True, records=4096, entry_on_time=True,
                acceptance=acceptance, simulated_us=row['simulated_us'],
                **{field: phase[field] for field in fields})


def stats(values):
    return dict(min=min(values), median=statistics.median(values), max=max(values))


try:
    for mode, label in planned:
        state['active_label'] = label
        save()
        state['calls'].append(one(mode, label))
        save()
    state.pop('active_label')
    state['tables'] = []
    for mode in ('sync', 'local-cross'):
        group = [row for row in state['calls'] if row['mode'] == mode]
        assert len(group) == count
        state['tables'].append(dict(mode=mode, n=count,
            simulated_us=stats([row['simulated_us'] for row in group]),
            body_budget=stats([row['body_budget'] for row in group]),
            gaps=[row['gap'] for row in group], spreads=[row['spread'] for row in group],
            boundary_accepted=all(row['acceptance']['accepted'] for row in group)))
    sync, clock = state['tables']
    state['accepted'] = clock['boundary_accepted']
    state['comparison'] = dict(
        body_median_delta_cycles=clock['body_budget']['median']-sync['body_budget']['median'],
        body_median_change_percent=100*(clock['body_budget']['median']/sync['body_budget']['median']-1),
        simulated_median_delta_us=clock['simulated_us']['median']-sync['simulated_us']['median'],
        simulated_median_change_percent=100*(clock['simulated_us']['median']/sync['simulated_us']['median']-1),
        paired_body_delta_cycles=[next(row['body_budget'] for row in state['calls'][2*i:2*i+2]
                                      if row['mode'] == 'local-cross') -
                                  next(row['body_budget'] for row in state['calls'][2*i:2*i+2]
                                      if row['mode'] == 'sync') for i in range(count)])
    state['status'] = 'auditing'
    save()
    # Reuse the saved-binary audit; this performs no compilation or simulation.
    labels = search_labels + [label for _, label in planned]
    assert len(labels) == len(set(labels))
    audit_source = COMPARE_AUDIT_SOURCE_VALUE
    audit_source = audit_source.replace('ROOT_VALUE', repr(str(root))).replace('REV_VALUE', repr(revision))
    audit_source = audit_source.replace('LABELS_VALUE', repr(labels)).replace('COMPACT_VALUE', 'True')
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        exec(compile(audit_source, 'audit_remote.py', 'exec'), {})
    audit = json.loads(capture.getvalue())
    assert audit['status'] == 'audited' and audit['case_count'] == len(labels)
    assert all(row['validated'] and row['records'] == 4096 for row in audit.pop('records'))
    # Bind the pre-run snapshot and exported statistics to the audited summaries.
    fields = ('C1', 'S2', 'L2', 'gap', 'spread', 'first_phase_budget', 'cross_ready_budget',
              'body_budget', 'body_end', 'limiting', 'boundary_failures', 'records', 'entry_on_time')
    snapshots = {row['target']: row for row in screening}
    groups = [(state['calls'], None)]
    for path in sorted(work.glob('t*.json')):
        evidence = path.read_bytes()
        data = json.loads(evidence)
        assert hashlib.sha256(evidence).hexdigest() == snapshots[data['target']]['sha256']
        calls = data['calls']
        assert data['body_budget'] == stats([row['body_budget'] for row in calls])
        assert data['simulated_us'] == stats([row['simulated_us'] for row in calls])
        assert data['gaps'] == [row['gap'] for row in calls]
        assert data['spreads'] == [row['spread'] for row in calls]
        groups.append((calls, data['target']))
    for calls, screened_target in groups:
        for row in calls:
            summary = json.loads((root/'cases'/row['label']/'summary.json').read_text())
            phase = summary['schedule_profile']
            mode = row.get('mode', 'local-cross')
            expected_target = screened_target if screened_target is not None else target if mode == 'local-cross' else 0
            assert summary['mode'] == mode and phase['targets'] == [expected_target]*4
            assert row['validated'] == summary['validated'] and row['simulated_us'] == summary['simulated_us']
            assert all(row[field] == phase[field] for field in fields)
            assert row['acceptance'] == schedule.evaluate_boundary(phase, **state['limits'])
    audit.update(screening_snapshot_verified=True, statistics_recomputed=True,
                 frozen_target_verified=True)
    state.update(status='completed', audit=audit)
    save()
except Exception as error:
    state.update(status='failed', failure_type=type(error).__name__)
    save()
    raise
encoded = json.dumps(state)
assert len(encoded.encode('utf-8')) < 45000
print(encoded, flush=True)
