"""Keep rejected targets and all samples; only runtime/data failures abort a batch."""
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

root, args = Path(ROOT_VALUE), ARGS_VALUE
revision, target = args['revision'], args['target']
assert json.loads((root/'build-summary.json').read_text())['runtime_commit'] == revision
sys.path.insert(0, str(root/'source/tools/unitest'))
from controller import direct_group_schedule as schedule

entry, peer, count = 32768, 65536, 3
work = root/'boundary-65536-gap-search-20261119'
work.mkdir(exist_ok=True)
key = 't' + str(target)
receipt = work/(key + '.json')
labels = ['boundary-65536-search-' + key + '-r' + str(i) for i in range(1, count + 1)]
assert not receipt.exists(), 'inspect existing candidate; never replay a target'
for label in labels:
    assert not (root/'cases'/label).exists(), 'case already exists; inspect saved evidence'
    suite = Path('/opt/sim/simulator/Testcases')/('schedule_' + revision[:8] + '_' + label)
    assert not suite.exists(), 'suite already exists; inspect saved evidence'
    assert not (work/(label + '.runner.json')).exists()
case_source = CASE_SOURCE_VALUE
runner = root/('boundary-case-' + hashlib.sha256(case_source.encode()).hexdigest()[:12] + '.py')
if runner.exists():
    assert runner.read_text() == case_source
else:
    runner.write_text(case_source)

state = dict(schema='tilexr.boundary.gap-search.v1', runtime_commit=revision,
             stage='candidate', status='running', target=target, budget=target-entry,
             entry_target=entry, peer_bytes=peer, total_bytes_per_rank=peer*64,
             ranks=64, blocks_per_rank=64, width=32, trace='perf', samples=count,
             limits=dict(epsilon=0, g_min=0, spread_max=299),
             exploratory=True, independent_verification=False,
             reference_basis='identity_assumption',
             physical_clock_calibration_verified=False,
             simulator_counter_frequency_verified=False,
             display_cycles_per_us=1650, conversion_source='user_provided', calls=[])
with receipt.open('x') as stream:
    json.dump(state, stream, indent=2)


def save():
    receipt.write_text(json.dumps(state, indent=2))


def one(label):
    spec = dict(mode='local-cross', trace='perf', peer_bytes=peer, label=label,
                width=32, entry_target=entry, targets=[target], plan_id=2,
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
    capture.write_text(json.dumps(dict(returncode=result.returncode,
                                       stdout=result.stdout, stderr=result.stderr)))
    assert result.returncode == 0 and not result.stderr, 'case runner failed; retain all evidence'
    row = json.loads(result.stdout)
    # The controller has already checked all rank payloads, guards and padding.
    assert row['runtime_commit'] == revision and row['mode'] == 'local-cross'
    assert row['trace'] == 'perf' and row['bytes_per_peer'] == peer
    assert row['ranks'] == row['block_dim'] == 64 and row['total_bytes_per_rank'] == peer*64
    assert row['validated'] and row['returncode'] == 0 and row['remaining_processes'] == []
    phase = row['schedule_profile']
    assert phase['version'] == 2 and phase['records'] == phase['entry_records'] == 4096
    assert phase['width'] == 32 and phase['entry_target'] == entry and phase['targets'] == [target]*4
    assert phase['reference_basis'] == 'identity_assumption' and phase['payload_valid']
    assert phase['entry_on_time'] and phase['entry_status_counts']['on_time'] == 4096
    assert phase['scheduling_on_time'] and phase['boundary_timing_usable']
    assert phase['scheduling_misses'] == phase['scheduling_errors'] == 0
    assert type(row['simulated_us']) in (int, float) and row['simulated_us'] > 0
    assert phase['gap'] == phase['S2'] - phase['C1']
    assert phase['spread'] == phase['L2'] - phase['S2']
    assert phase['body_budget'] == phase['body_end'] - entry
    acceptance = schedule.evaluate_boundary(phase, **state['limits'])
    assert acceptance['accepted'] == (phase['gap'] >= 0 and phase['spread'] <= 299)
    fields = ('C1', 'S2', 'L2', 'gap', 'spread', 'first_phase_budget',
              'cross_ready_budget', 'body_budget', 'body_end', 'limiting', 'boundary_failures')
    return dict(label=label, validated=True, entry_on_time=True, records=4096,
                scheduling_misses=0, scheduling_errors=0, acceptance=acceptance,
                simulated_us=row['simulated_us'], **{field: phase[field] for field in fields})


def stats(values):
    return dict(min=min(values), median=statistics.median(values), max=max(values))


try:
    for label in labels:
        state['active_label'] = label
        save()
        state['calls'].append(one(label))
        save()
    state.pop('active_label')
    state.update(status='completed', accepted=all(row['acceptance']['accepted'] for row in state['calls']),
                 simulated_us=stats([row['simulated_us'] for row in state['calls']]),
                 body_budget=stats([row['body_budget'] for row in state['calls']]),
                 gaps=[row['gap'] for row in state['calls']],
                 spreads=[row['spread'] for row in state['calls']])
    save()
except Exception as error:
    state.update(status='failed', failure_type=type(error).__name__)
    save()
    raise
encoded = json.dumps(state)
assert len(encoded.encode('utf-8')) < 12000
print(encoded, flush=True)
