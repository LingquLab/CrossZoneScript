"""Run the prior operator matrix and capture timestamp checks on the rebased objects."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

args, root = ARGS_VALUE, Path(ROOT_VALUE)
build = json.loads((root/'build-summary.json').read_text())
assert build['status'] == 'compiled' and build['runtime_commit'] == args['revision']
batch = args['batch']
receipt_path = root/('batch-'+batch+'.json')
sys.path.insert(0, str(root/'source/tools/unitest'))
from controller import direct_group_schedule as schedule, memory_sst as mem

jobs = [('memory', (8, 'small', 'memory', mode))
        for mode in ('ordered', 'data-as-flag', 'sentinel')]
jobs.extend([
    ('memory', (64, 'small', 'memory', 'two-group')),
    ('memory', (32, 'small', 'multi-b8', 'sentinel')),
    ('memory', (64, 'small', 'group', 'two-group')),
    ('clock', dict(variant='clock', size='small', target=11264,
                   frequency=1000000000, label='final-clock-small')),
    ('boundary', dict(mode='local-cross', trace='perf', peer_bytes=512,
                      label='final-boundary-small', width=32, entry_target=32768,
                      plan_id=2, targets=[39872], simulation_timeout=900)),
])

scripts = {}
for kind, source in RUNNERS_VALUE.items():
    path = root/('runner-'+kind+'-'+hashlib.sha256(source.encode()).hexdigest()[:12]+'.py')
    if path.exists():
        assert path.read_text() == source
    else:
        path.write_text(source)
    scripts[kind] = path

result = dict(batch=batch, runtime_commit=args['revision'], main=build['main'],
              case_count=len(jobs), status='running', records=[])
with receipt_path.open('x') as stream:
    json.dump(result, stream, indent=2)


def save():
    pending = receipt_path.with_suffix('.tmp')
    pending.write_text(json.dumps(result, indent=2))
    pending.replace(receipt_path)


def collect(kind, spec, row):
    report = json.loads(Path(row['report']).read_text())
    cfg = report['simulator']
    binary, _, layout = mem.qualify(cfg)
    ranks = row.get('ranks', 64)
    total = row['total_bytes_per_rank']
    assert binary['runtime_commit'] == row['runtime_commit'] == args['revision']
    assert binary['kernel']['sha256'] == row['kernel_sha256']
    assert layout['kernel_args_size'] == 96 and cfg['iterations'] == 1
    assert report['case']['world_size'] == ranks and not report['run_options']['profiling']['enabled']
    records = report['operator_result'].get('ranks', [])
    if row['validated']:
        assert report['status'] == 'passed' and row['returncode'] == 0
        assert {r['rank'] for r in records} == set(range(ranks)) and len(records) == ranks
        assert all(r['status'] == r['padding'] == r['guards'] == 'passed' and
                   r['checked_bytes'] == total for r in records)
        assert row['simulated_us'] > 0
        assert mem.digest(Path(cfg['suite_output'])/'kernel.o') == row['kernel_sha256']
        assert report['phases']['simulation']['loaded_libraries']['state'] == 'sst_library_mappings_verified'
    geometry = report['phases'].get('prepare', {})
    if geometry:
        _, blocks = mem.launch_geometry(binary, report['case'], cfg['iterations'])
        assert geometry['block_dim'] == blocks
    response = dict(kind=kind, spec=spec, validated=row['validated'], ranks=ranks,
        block_dim=geometry.get('block_dim'), checked_ranks=len(records), total_bytes_per_rank=total,
        kernel_sha256=row['kernel_sha256'], simulated_us=row['simulated_us'],
        returncode=row['returncode'], remaining_processes=row['remaining_processes'],
        failed_phase=row['failed_phase'], error=row['error'], label=Path(row['report']).parent.name)
    if row.get('fatal_lines'):
        response['fatal_lines'] = row['fatal_lines']
    phase = row.get('schedule_profile')
    if phase:
        keys = ('records', 'entry_on_time', 'scheduling_misses', 'scheduling_errors', 'gap',
                'spread', 'C1', 'S2', 'L2', 'body_budget', 'reference_basis', 'targets')
        assert phase['records'] == 4096 and phase['version'] == 2
        assert phase['gap'] == phase['S2']-phase['C1'] and phase['spread'] == phase['L2']-phase['S2']
        response['phase'] = {key: phase[key] for key in keys}
        if spec['mode'] == 'local-cross':
            response['boundary_accepted'] = schedule.evaluate_boundary(
                phase, epsilon=0, g_min=0, spread_max=299)['accepted']
    if kind == 'clock':
        phase = row.get('clock_phase') or {}
        response['clock_phase'] = {k: v for k, v in phase.items()
                                   if isinstance(v, (int, str, bool)) and k != 'path'}
    return response


for i, (kind, spec) in enumerate(jobs, 1):
    result['active'] = dict(kind=kind, spec=spec)
    save()
    argv = list(map(str, spec)) if kind == 'memory' else [json.dumps(spec)]
    call = subprocess.run([sys.executable, str(scripts[kind]), str(root), *argv],
                          capture_output=True, text=True, timeout=1100)
    (root/(f'batch-{batch}-{i:02d}.runner.json')).write_text(json.dumps(
        dict(returncode=call.returncode, stdout=call.stdout, stderr=call.stderr)))
    if call.returncode or call.stderr:
        result.update(status='runner_failed', runner_error=call.stderr.splitlines()[-3:])
        save()
        print(json.dumps(result))
        raise SystemExit(1)
    row = collect(kind, spec, json.loads(call.stdout))
    result['records'].append(row)
    save()
    if row['remaining_processes'] or row['returncode'] is None:
        result.update(status='execution_receipt_incomplete')
        save()
        print(json.dumps(result))
        raise SystemExit(1)
result.pop('active')
result.update(status='completed', passed=sum(row['validated'] for row in result['records']))
save()
print(json.dumps(result, separators=(',', ':')))

if not all(row['validated'] and row.get('boundary_accepted', True) for row in result['records']):
    raise SystemExit(1)
