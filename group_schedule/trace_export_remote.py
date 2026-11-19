"""Verify saved per-AIV records, retain full timing JSON, return communication timestamps."""
import hashlib
import json
from pathlib import Path
import sys

revision = '347747af83daa7d3054563626d4dbef85a0ea729'
root = Path('/opt/sim/alltoall-group-schedule-347747af-20261118')
label = TRACE_LABEL_VALUE
output = root / 'cases' / label
sys.path.insert(0, str(root / 'source/tools/unitest'))
from controller import direct_group_schedule as schedule

report = json.loads((output / 'result.json').read_text())
summary = json.loads((output / 'summary.json').read_text())
cfg = report['simulator']['schedule']
assert summary['runtime_commit'] == revision
assert report['status'] == 'passed' and summary['validated']
assert summary['trace'] == 'perf' and cfg['version'] == 2
assert summary['total_bytes_per_rank'] == 4194304 and summary['bytes_per_peer'] == 65536
assert summary['returncode'] == 0 and summary['remaining_processes'] == []
assert cfg['model_source'] == 'sst_identity'
assert cfg['entry_target'] == 32768 and cfg['width'] == 32
ranks = sorted(report['operator_result']['ranks'], key=lambda row: row['rank'])
assert [row['rank'] for row in ranks] == list(range(64))
records = []
for rank_row in ranks:
    rank = rank_row['rank']
    assert rank_row['status'] == rank_row['guards'] == rank_row['padding'] == 'passed'
    size = len(schedule.initial(cfg, rank))
    with (output / ('rank%d-output.bin' % rank)).open('rb') as stream:
        stream.seek(-size, 2)
        rows = schedule.decode(stream.read(size), cfg, rank, False)
    assert rows == rank_row['schedule_records']
    assert [row['peer'] for row in rows] == list(range(64))
    assert all(row['entry_on_time'] and row['on_time'] and row['clock_valid'] for row in rows)
    records.extend(rows)
phase = schedule.summarize(ranks, cfg, False)
assert phase == report['schedule_profile'] == summary['schedule_profile']
assert len(records) == phase['records'] == 4096
assert phase['scheduling_misses'] == phase['scheduling_errors'] == 0
fields = ('rank', 'block', 'peer', 'same_group', 'gated', 'wave', 'version', 'plan_id',
          'entry_arrival', 'entry_release', 'entry_status', 'entry_deadline',
          'entry_late_cutoff', 'entry_target_reference', 'gate_arrival', 'gate_release',
          'status', 'deadline', 'late_cutoff', 'target_reference',
          'communication_start', 'communication_end', 'body_end')
metadata = dict(schema='tilexr.boundary.perf-timestamps.v1', runtime_commit=revision,
    label=label, mode=summary['mode'], trace='perf', peer_bytes=65536,
    total_bytes_per_rank=4194304, ranks=64, blocks_per_rank=64, records=4096,
    time_unit='raw_system_counter_cycles', reference_basis='identity_assumption',
    physical_clock_calibration_verified=False, simulator_counter_frequency_verified=False,
    entry_target=cfg['entry_target'], phase_targets=cfg['targets'],
    simulation_launched=False, source_records_redecoded=True,
    metrics={key: phase[key] for key in ('C1', 'S2', 'L2', 'gap', 'spread', 'body_budget')},
    simulated_us=summary['simulated_us'])
complete = dict(metadata, records=[{key: row[key] for key in fields} for row in records])
encoded = (json.dumps(complete, separators=(',', ':'), sort_keys=True) + '\n').encode('utf-8')
destination = root / 'exports' / (label + '-perf-timestamps.json')
destination.parent.mkdir(exist_ok=True)
if destination.exists():
    assert destination.read_bytes() == encoded, 'preserve existing export'
else:
    with destination.open('xb') as stream:
        stream.write(encoded)
entry = cfg['entry_target']
intervals = [[row['communication_start'] - entry, row['communication_end'] - entry]
             for row in records]
assert all(type(value) is int and value >= 0 for interval in intervals for value in interval)
assert all(start <= end for start, end in intervals)
result = dict(metadata, complete_green_json=str(destination), complete_green_json_bytes=len(encoded),
    complete_green_json_sha256=hashlib.sha256(encoded).hexdigest(),
    returned_fields=['communication_start_minus_entry', 'communication_end_minus_entry'],
    record_order='rank-major then peer-major; index=rank*64+peer; block=peer',
    communication_intervals=intervals)
text = json.dumps(result, separators=(',', ':'))
assert len(text.encode('utf-8')) <= 58000, 'retain full export locally if bounded result does not fit'
print(text)
