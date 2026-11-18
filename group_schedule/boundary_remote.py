"""共同E实验：先sync完整耗时，再大T采样，冻结后独立验证；不自动重跑。"""
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

root,args=Path(ROOT_VALUE),ARGS_VALUE
assert json.loads((root/'build-summary.json').read_text())['runtime_commit']==args['revision']
sys.path.insert(0,str(root/'source/tools/unitest'))
from controller import direct_group_schedule as schedule

peer=args['peer_bytes'];stage=args['stage'];entry=32768
prefix='margin-' if stage.startswith('margin-') else ''
phase_stage=stage[len(prefix):]
work=root/('boundary-'+str(peer));work.mkdir(exist_ok=True)
receipt=work/(stage+'.json')
assert not receipt.exists(),'inspect existing stage before retry'
case_source=CASE_SOURCE_VALUE
runner=root/('boundary-case-'+hashlib.sha256(case_source.encode()).hexdigest()[:12]+'.py')
if runner.exists():assert runner.read_text()==case_source
else:runner.write_text(case_source)

def read(name):
    return json.loads((work/(name+'.json')).read_text())

def write(name,data):
    target=work/(name+'.json');assert not target.exists()
    target.write_text(json.dumps(data,indent=2))

def one(mode,label,budget=None,delay=None):
    label=prefix+label
    spec=dict(mode=mode,trace='perf',peer_bytes=peer,label='boundary-'+str(peer)+'-'+label,
              width=32,entry_target=entry,plan_id=2,max_wait_cycles=1000000,simulation_timeout=1800)
    if budget is not None:spec['targets']=[entry+budget]
    if delay is not None:spec['delay_cycles']=delay
    result=subprocess.run([sys.executable,str(runner),str(root),json.dumps(spec)],
                          capture_output=True,text=True,timeout=2000)
    # Preserve output even for a runner failure; never replay an unknown-effect case.
    (work/(label+'.runner.json')).write_text(json.dumps(dict(
        returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)))
    assert result.returncode==0 and not result.stderr,'case runner failed; inspect saved evidence'
    row=json.loads(result.stdout)
    assert row['validated'] and row['returncode']==0 and row['remaining_processes']==[], 'payload or cleanup failed'
    phase=row['schedule_profile']
    assert phase['version']==2 and phase['records']==4096 and phase['width']==32
    write(label,row)
    if delay is None:
        assert phase['entry_on_time'] and phase['scheduling_on_time'] and phase['boundary_timing_usable'], \
            'invalid entry/gate/timing; retain sample and stop dependent calibration'
    return row

def compact(row):
    phase=row['schedule_profile']
    keep=('entry_target','targets','entry_on_time','entry_status_counts','scheduling_misses',
          'scheduling_errors','gap','spread','first_phase_budget','cross_ready_budget','body_budget',
          'C1','S2','L2','limiting','boundary_failures','boundary_observed_valid','reference_basis')
    return dict(label=Path(row['report']).parent.name,validated=row['validated'],
                simulated_us=row['simulated_us'],**{k:phase[k] for k in keep})

rows=[]
if phase_stage=='baseline':
    rows.append(one('sync','sync-warmup'))
    rows.extend(one('sync','base-r'+str(i)) for i in (1,2))
    training=[r['schedule_profile'] for r in rows[1:]]
    # Explicit experimental floor, not an assumed universal margin.
    guard=max(256,max(s['body_budget'] for s in training)-min(s['body_budget'] for s in training))
    boot=schedule.bootstrap_boundary_budget(training,bootstrap_guard=guard)
    result=dict(bootstrap=boot,calls=[compact(r) for r in rows])
elif phase_stage=='bootstrap':
    boot=read('baseline')['bootstrap']
    rows=[one('local-cross','boot-r'+str(i),boot['B_boot']) for i in (1,2,3)]
    training=[r['schedule_profile'] for r in rows]
    phase_floor=1024 if prefix else 256
    phase_guard=max(phase_floor,max(s['first_phase_budget'] for s in training)-min(s['first_phase_budget'] for s in training))
    ready_guard=max(256,max(s['cross_ready_budget'] for s in training)-min(s['cross_ready_budget'] for s in training))
    frozen=schedule.freeze_boundary_budget(training,phase_guard=phase_guard,ready_guard=ready_guard)
    frozen.update(epsilon=0,g_min=0,spread_max=max(s['spread'] for s in training)+256,
                  entry=entry,peer_bytes=peer,ranks=64,blocks=64,reference_basis='identity_assumption',
                  runtime_commit=args['revision'],trace='perf',bootstrap=boot)
    if prefix:frozen.update(calibration_round='margin-1024',phase_guard_floor=1024)
    write(prefix+'frozen',frozen)
    result=dict(frozen=frozen,calls=[compact(r) for r in rows])
elif phase_stage=='verify':
    frozen=read(prefix+'frozen')
    assert frozen['entry']==entry and frozen['peer_bytes']==peer and frozen['runtime_commit']==args['revision']
    rows=[one('local-cross','clock-warmup',frozen['B']),one('no-gate','no-gate-warmup')]
    # Fresh fixtures, predetermined order and three independent samples per mode.
    orders=(('sync','no-gate','local-cross'),('local-cross','no-gate','sync'),('no-gate','sync','local-cross'))
    measured=[];acceptance=[]
    for i,modes in enumerate(orders,1):
        for mode in modes:
            row=one(mode,mode+'-r'+str(i),frozen['B'] if mode=='local-cross' else None)
            rows.append(row);measured.append(row)
            if mode=='local-cross':
                acceptance.append(schedule.evaluate_boundary(row['schedule_profile'],epsilon=frozen['epsilon'],
                    g_min=frozen['g_min'],spread_max=frozen['spread_max']))
    tables=[]
    for mode in ('sync','no-gate','local-cross'):
        group=[r for r in measured if r['mode']==mode]
        us=[r['simulated_us'] for r in group];body=[r['schedule_profile']['body_budget'] for r in group]
        tables.append(dict(mode=mode,n=len(group),simulated_us=dict(min=min(us),median=statistics.median(us),max=max(us)),
            body_budget=dict(min=min(body),median=statistics.median(body),max=max(body)),
            gaps=[r['schedule_profile']['gap'] for r in group],spreads=[r['schedule_profile']['spread'] for r in group],
            entry_on_time=all(r['schedule_profile']['entry_on_time'] for r in group)))
    result=dict(frozen=frozen,accepted=all(a['accepted'] for a in acceptance),acceptance=acceptance,
                tables=tables,calls=[compact(r) for r in rows])
else:
    frozen=read('frozen');delay=[0]*64;delay[63]=entry+1024
    rows=[one('local-cross','late-entry-r63',frozen['B'],delay)]
    acceptance=schedule.evaluate_boundary(rows[0]['schedule_profile'],epsilon=0,g_min=0,spread_max=frozen['spread_max'])
    assert not acceptance['accepted'] and not rows[0]['schedule_profile']['entry_on_time']
    result=dict(expected_rejection=True,acceptance=acceptance,calls=[compact(r) for r in rows])
result.update(runtime_commit=args['revision'],stage=stage,peer_bytes=peer,total_bytes_per_rank=64*peer,
              physical_clock_calibration_verified=False,simulator_counter_frequency_verified=False)
receipt.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
if phase_stage=='verify':assert result['accepted'],'frozen clock boundary rejected; retain all evidence'
