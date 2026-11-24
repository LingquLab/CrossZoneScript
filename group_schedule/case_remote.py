"""运行一组固定64P调度计划；完整结果留在容器，返回payload与调度的独立汇总。"""
import json
from pathlib import Path
import sys

root,spec=Path(sys.argv[1]),json.loads(sys.argv[2])
source=root/'source';sys.path.insert(0,str(source/'tools/unitest'))
from controller import memory_sst as mem,direct_group_schedule as schedule
from controller.case import load_case

mode,trace=spec['mode'],spec['trace']
key='original' if mode=='original' else mode+'-'+trace
build=json.loads((root/('build-'+key+'.json')).read_text())
revision=build['runtime_commit'];output=root/'cases'/spec['label']
suite=Path('/opt/sim/simulator/Testcases')/('schedule_'+revision[:8]+'_'+spec['label'])
assert not suite.exists() and not output.exists(),'preserve prior results'
output.mkdir(parents=True)
raw=json.loads((source/'tools/unitest/cases/alltoall-mem-two-group.json').read_text())
raw.update(world_size=64,elements_per_peer=spec['peer_bytes']//4,id='schedule-'+spec['label'])
case_path=output/'case.json';case_path.write_text(json.dumps(raw))
case=load_case(case_path,list(range(64)))
cfg=dict(schema='tilexr.unitest.sst.memory.v1',rank_size=64,suite_output=str(suite),
    build_report=str(root/('build-'+key+'.json')),environment_report=str(root/'environment-report.json'),
    threads=64,simulation_timeout=spec.get('simulation_timeout',900),iterations=1)
if mode!='original':
    width=spec.get('width',32);targets=spec.get('targets',[0]*(32//width))
    cfg['schedule']=schedule.identity_schedule(mode,targets,width=width,
        plan_id=spec.get('plan_id',1),max_wait_cycles=spec.get('max_wait_cycles',1000000),
        delay_cycles=spec.get('delay_cycles'))
    if 'entry_target' in spec:
        cfg['schedule']=schedule.boundary_schedule(cfg['schedule'],spec['entry_target'])
    if 'model_reference_error_cycles' in spec or 'model_rate_q32' in spec:
        cfg['schedule']['model_source']='ready_drift_snapshot'
        for rank, model in enumerate(cfg['schedule']['models']):
            sign=1 if rank%2 else -1
            model['reference_anchor']=sign*spec.get('model_reference_error_cycles',0)
            model['rate_correction_q32']=sign*spec.get('model_rate_q32',0)
        schedule.validate(cfg['schedule'])
(output/'experiment.json').write_text(json.dumps(spec,indent=2))
(output/'config.json').write_text(json.dumps(cfg,indent=2))
cfg=mem.load_config(output/'config.json',cfg)
report=mem.run(cfg,case,dict(mode='single',profiling=dict(enabled=False)),{},output)
receipt=json.loads((suite/'process_receipt.json').read_text()) if (suite/'process_receipt.json').exists() else {}
ranks=report.get('operator_result',{}).get('ranks',[])
phase=report.get('schedule_profile')
row=dict(runtime_commit=revision,kernel_sha256=build['kernel']['sha256'],mode=mode,trace=trace,
    ranks=64,block_dim=64,bytes_per_peer=spec['peer_bytes'],total_bytes_per_rank=64*spec['peer_bytes'],
    validated=report['status']=='passed' and len(ranks)==64,
    simulated_us=report.get('phases',{}).get('simulation',{}).get('simulated_us'),
    schedule_profile=phase,error=report.get('error'),failed_phase=report.get('failed_phase'),
    returncode=receipt.get('returncode'),remaining_processes=receipt.get('remaining_processes'),
    report=str(output/'result.json'))
(output/'summary.json').write_text(json.dumps(row,indent=2))
if phase:
    compact=dict(phase);compact.pop('ranks',None);row['schedule_profile']=compact
print(json.dumps(row))
