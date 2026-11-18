"""32P固定32AIV对照；保留完整数据和阶段记录，仅输出聚合统计。"""
import json
from pathlib import Path
import sys

root,spec=Path(sys.argv[1]),json.loads(sys.argv[2])
source=root/'source';sys.path.insert(0,str(source/'tools/unitest'))
from controller import memory_sst as mem,direct_group_clock as clock,direct_group_profile as profile
from controller.case import load_case

mode=spec['mode']; build=json.loads((root/('build-'+mode+'.json')).read_text())
revision=build['runtime_commit'];output=root/'cases'/spec['label']
suite=Path('/opt/sim/simulator/Testcases')/('peer32_'+revision[:8]+'_'+spec['label'])
assert not suite.exists() and not output.exists(),'preserve prior results'
output.mkdir(parents=True)
raw=json.loads((source/'tools/unitest/cases/alltoall-mem-sentinel.json').read_text())
raw.update(world_size=32,elements_per_peer=spec['peer_bytes']//4,id='peer32-'+spec['label'])
case_path=output/'case.json';case_path.write_text(json.dumps(raw))
case=load_case(case_path,list(range(32)))
cfg=dict(schema='tilexr.unitest.sst.memory.v1',rank_size=32,suite_output=str(suite),
    build_report=str(root/('build-'+mode+'.json')),environment_report=str(root/'environment-report.json'),
    threads=64,simulation_timeout=900,iterations=1)
if mode=='profile':cfg['clock']=clock.identity_schedule(0,20000,1000000000)
(output/'config.json').write_text(json.dumps(cfg,indent=2))
cfg=mem.load_config(output/'config.json',cfg)
report=mem.run(cfg,case,dict(mode='single',profiling=dict(enabled=False)),{},output)
receipt=json.loads((suite/'process_receipt.json').read_text()) if (suite/'process_receipt.json').exists() else {}
ranks=report.get('operator_result',{}).get('ranks',[])
phase=report.get('phase_profile');windows=None
if phase:
    spans=[max(r['stamps'][3] for r in rank['phase_records'])-
           min(r['stamps'][2] for r in rank['phase_records']) for rank in ranks]
    windows=profile.stats(spans)
row=dict(runtime_commit=revision,kernel_sha256=build['kernel']['sha256'],mode=mode,
    ranks=32,block_dim=32,bytes_per_peer=spec['peer_bytes'],total_bytes_per_rank=32*spec['peer_bytes'],
    validated=report['status']=='passed' and len(ranks)==32,
    simulated_us=report.get('phases',{}).get('simulation',{}).get('simulated_us'),
    phase_profile=phase,communication_window=windows,error=report.get('error'),
    failed_phase=report.get('failed_phase'),returncode=receipt.get('returncode'),
    remaining_processes=receipt.get('remaining_processes'),report=str(output/'result.json'))
(output/'summary.json').write_text(json.dumps(row,indent=2))
if phase:
    compact=dict(phase);compact.pop('ranks',None);row['phase_profile']=compact
print(json.dumps(row))
