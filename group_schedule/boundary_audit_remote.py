"""重算四档与独立余量复验的预算和验收，保留阴性结果；不修改或重跑实验。"""
import contextlib
import io
import json
from pathlib import Path
import statistics
import sys

root, revision = Path(BOUNDARY_ROOT_VALUE), BOUNDARY_REV_VALUE
margin_peers = MARGIN_PEERS_VALUE
sys.path.insert(0, str(root / 'source/tools/unitest'))
from controller import direct_group_schedule as schedule

peers = (512, 8192, 65536, 262144)
orders = (('sync', 'no-gate', 'local-cross'), ('local-cross', 'no-gate', 'sync'),
          ('no-gate', 'sync', 'local-cross'))
suffixes = ['sync-warmup', 'base-r1', 'base-r2'] + ['boot-r' + str(i) for i in (1, 2, 3)]
suffixes += ['clock-warmup', 'no-gate-warmup']
suffixes += [mode + '-r' + str(i) for i, modes in enumerate(orders, 1) for mode in modes]
labels = ['boundary-' + str(peer) + '-' + suffix for peer in peers for suffix in suffixes]
labels += ['boundary-' + str(peer) + '-margin-' + suffix
           for peer in margin_peers for suffix in suffixes[3:]]
fault_label = 'boundary-512-late-entry-r63'
labels.append(fault_label)
assert len(labels) == len(set(labels)) == 69 + 14 * len(margin_peers)
actual_labels = {path.name for path in (root / 'cases').iterdir()}
assert actual_labels == set(labels), 'missing or unexpected case; inspect inventory before completion'

# This audit rechecks original/deployed objects, qualified source/environment assets,
# loaded libraries, launch geometry, saved payloads/guards/padding and all v2 records.
audit_source = AUDIT_SOURCE_VALUE
audit_source = audit_source.replace('ROOT_VALUE', repr(str(root))).replace('REV_VALUE', repr(revision))
audit_source = audit_source.replace('LABELS_VALUE', repr(labels)).replace('COMPACT_VALUE', 'True')
capture = io.StringIO()
with contextlib.redirect_stdout(capture):
    exec(compile(audit_source, 'audit_remote.py', 'exec'), {})
audit = json.loads(capture.getvalue())
assert audit['status'] == 'audited' and audit['runtime_commit'] == revision
assert audit['case_count'] == len(labels) and audit['all_owned_processes_clean']


def load(path):
    return json.loads(path.read_text())


def case(peer, suffix, mode, budget=None, fault=False):
    label = 'boundary-' + str(peer) + '-' + suffix
    output = root / 'cases' / label
    summary = load(output / 'summary.json')
    report = load(output / 'result.json')
    cfg = report['simulator']['schedule']
    phase = summary['schedule_profile']
    spec = load(output / 'experiment.json')
    assert summary['mode'] == mode and summary['trace'] == 'perf'
    assert summary['bytes_per_peer'] == peer and summary['total_bytes_per_rank'] == 64 * peer
    assert summary['validated'] and summary['returncode'] == 0 and summary['remaining_processes'] == []
    assert phase == report['schedule_profile']
    assert phase['version'] == cfg['version'] == 2 and phase['records'] == phase['entry_records'] == 4096
    assert phase['width'] == cfg['width'] == 32 and phase['entry_target'] == cfg['entry_target'] == 32768
    assert cfg['model_source'] == 'sst_identity' and phase['reference_basis'] == 'identity_assumption'
    target = 0 if budget is None else 32768 + budget
    assert cfg['targets'] == phase['targets'] == [target] * 4
    assert phase['boundary_timing_usable'] and phase['scheduling_errors'] == 0
    assert phase['gap'] == phase['S2'] - phase['C1']
    assert phase['spread'] == phase['L2'] - phase['S2']
    assert phase['first_phase_budget'] == phase['C1'] - 32768
    assert phase['body_budget'] == phase['body_end'] - 32768
    assert spec['mode'] == mode and spec['peer_bytes'] == peer and spec['entry_target'] == 32768
    if fault:
        assert spec['delay_cycles'] == [0] * 63 + [33792]
        assert not phase['entry_on_time'] and phase['entry_late_arrivals'] > 0
    else:
        assert phase['entry_on_time'] and phase['entry_status_counts'] == dict(on_time=4096, late=0, horizon=0, rollback=0)
        assert phase['scheduling_on_time'] and phase['scheduling_misses'] == 0
    return summary


def stage(peer, name, calls):
    data = load(root / ('boundary-' + str(peer)) / (name + '.json'))
    assert data['runtime_commit'] == revision and data['stage'] == name
    assert data['peer_bytes'] == peer and data['total_bytes_per_rank'] == 64 * peer
    assert len(data['calls']) == len(calls)
    for stored, summary in zip(data['calls'], calls):
        assert stored['label'] == Path(summary['report']).parent.name
        assert stored['validated'] == summary['validated']
        assert stored['simulated_us'] == summary['simulated_us']
        for key, value in stored.items():
            if key not in ('label', 'validated', 'simulated_us'):
                assert value == summary['schedule_profile'][key]
    return data


def guard(samples, field, floor=256):
    values = [sample[field] for sample in samples]
    return max(floor, max(values) - min(values))


def audit_round(peer, boot, prefix='', phase_floor=256):
    training_calls = [case(peer, prefix + 'boot-r' + str(i), 'local-cross', boot['B_boot']) for i in (1, 2, 3)]
    training = stage(peer, prefix + 'bootstrap', training_calls)
    samples = [row['schedule_profile'] for row in training_calls]
    frozen = schedule.freeze_boundary_budget(samples, phase_guard=guard(samples, 'first_phase_budget', phase_floor),
                                             ready_guard=guard(samples, 'cross_ready_budget'))
    frozen.update(epsilon=0, g_min=0, spread_max=max(sample['spread'] for sample in samples) + 256,
                  entry=32768, peer_bytes=peer, ranks=64, blocks=64, reference_basis='identity_assumption',
                  runtime_commit=revision, trace='perf', bootstrap=boot)
    if prefix:
        frozen.update(calibration_round='margin-1024', phase_guard_floor=1024)
    assert frozen == training['frozen'] == load(root / ('boundary-' + str(peer)) / (prefix + 'frozen.json'))
    calls = [case(peer, prefix + 'clock-warmup', 'local-cross', frozen['B']),
             case(peer, prefix + 'no-gate-warmup', 'no-gate')]
    measured = []
    acceptance = []
    for i, modes in enumerate(orders, 1):
        for mode in modes:
            row = case(peer, prefix + mode + '-r' + str(i), mode, frozen['B'] if mode == 'local-cross' else None)
            calls.append(row)
            measured.append(row)
            if mode == 'local-cross':
                acceptance.append(schedule.evaluate_boundary(row['schedule_profile'], epsilon=frozen['epsilon'],
                    g_min=frozen['g_min'], spread_max=frozen['spread_max']))
    verify = stage(peer, prefix + 'verify', calls)
    assert verify['frozen'] == frozen and verify['acceptance'] == acceptance
    accepted = all(row['accepted'] for row in acceptance)
    assert type(verify['accepted']) is bool and verify['accepted'] == accepted
    tables = []
    for mode in ('sync', 'no-gate', 'local-cross'):
        group = [row for row in measured if row['mode'] == mode]
        us = [row['simulated_us'] for row in group]
        body = [row['schedule_profile']['body_budget'] for row in group]
        tables.append(dict(mode=mode, n=3,
            simulated_us=dict(min=min(us), median=statistics.median(us), max=max(us)),
            body_budget=dict(min=min(body), median=statistics.median(body), max=max(body)),
            gaps=[row['schedule_profile']['gap'] for row in group],
            spreads=[row['schedule_profile']['spread'] for row in group], entry_on_time=True))
    assert tables == verify['tables']
    return dict(frozen=frozen, accepted=accepted, acceptance=acceptance, tables=tables)


sizes = []
for peer in peers:
    baseline_calls = [case(peer, suffix, 'sync') for suffix in suffixes[:3]]
    baseline = stage(peer, 'baseline', baseline_calls)
    samples = [row['schedule_profile'] for row in baseline_calls[1:]]
    boot = schedule.bootstrap_boundary_budget(samples, bootstrap_guard=guard(samples, 'body_budget'))
    assert boot == baseline['bootstrap']
    row = dict(peer_bytes=peer, total_bytes_per_rank=64 * peer, **audit_round(peer, boot))
    if peer in margin_peers:
        row['margin'] = audit_round(peer, boot, prefix='margin-', phase_floor=1024)
    sizes.append(row)

fault_call = case(512, 'late-entry-r63', 'local-cross', sizes[0]['frozen']['B'], fault=True)
fault = stage(512, 'late-entry', [fault_call])
rejection = schedule.evaluate_boundary(fault_call['schedule_profile'], epsilon=0, g_min=0,
                                     spread_max=sizes[0]['frozen']['spread_max'])
assert fault['expected_rejection'] and fault['acceptance'] == rejection and not rejection['accepted']
assert all(row['records'] == 4096 and row['ranks'] == 64 for row in audit['records'])
audit.pop('records')
audit.update(read_only=True, inventory_exact=True, incomplete_labels=[],
             budgets_recomputed=True, frozen_targets_verified=True, statistics_recomputed=True,
             payload_passed_cases=len(labels), measured_calls=36 + 9 * len(margin_peers),
             calibration_calls=20 + 3 * len(margin_peers), independent_preruns=12 + 2 * len(margin_peers),
             expected_fault_cases=1, fault_expected_rejection_verified=True, margin_peers=margin_peers,
             audited_labels=labels, records_per_case=4096, total_records=4096 * len(labels),
             initial_boundary_accepted=all(row['accepted'] for row in sizes),
             final_configuration_accepted=all(row.get('margin', row)['accepted'] for row in sizes), sizes=sizes)
print(json.dumps(audit), flush=True)
