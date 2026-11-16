"""Run one fresh exact-binary AllToAll case and retain full local evidence.
运行单个精确对象用例，保留完整本地证据并导出精简结果。
"""
import hashlib,json,pathlib,subprocess,sys
root=pathlib.Path(sys.argv[1]); n=int(sys.argv[2]); size=sys.argv[3]; variant=sys.argv[4]; algorithm=sys.argv[5]
assert n in (8,16,32,64) and size in ('small','512k','4m')
assert algorithm in ('ordered','data-as-flag','sentinel','two-group')
assert variant in ('memory','group','multi-b1','multi-b2','multi-b4','multi-b8','multi-b16','multi-b32')
if variant=='group':assert n==64 and algorithm=='two-group'
if variant.startswith('multi'):assert n==32 and algorithm=='sentinel'
s=root/'source';sys.path.insert(0,str(s/'tools/unitest'))
from controller import memory_sst as mem
from controller.case import load_case
label=f'{n}p-{size}-{variant}-{algorithm}'
output=root/'regression'/label
suite=pathlib.Path('/opt/sim/simulator/Testcases')/('rebase_db76effb_'+label)
assert not output.exists() and not suite.exists(), 'preserve earlier attempt; inspect before resuming'
output.mkdir(parents=True)
total=n*512 if size=='small' else 524288 if size=='512k' else 4194304
raw=json.loads((s/'tools/unitest/cases'/('alltoall-mem-'+algorithm+'.json')).read_text())
raw.update(world_size=n,elements_per_peer=total//n//4,id='rebase-'+label)
case_path=output/'case.json';case_path.write_text(json.dumps(raw,indent=2));case=load_case(case_path,list(range(n)))
config=dict(schema='tilexr.unitest.sst.memory.v1',rank_size=n,suite_output=str(suite),build_report=str(root/('build-'+variant+'.json')),environment_report=str(root/'environment-report.json'),threads=n,simulation_timeout=600,iterations=1)
config_path=output/'config.json';config_path.write_text(json.dumps(config,indent=2))
config=mem.load_config(config_path,config)
report=mem.run(config,case,dict(mode='single',profiling=dict(enabled=False)),{},output)
build=json.loads((root/('build-'+variant+'.json')).read_text())
ranks=report.get('operator_result',{}).get('ranks',[])
evidence=report.get('tool_result',{}).get('sst_result',{})
receipt=json.loads((suite/'process_receipt.json').read_text()) if (suite/'process_receipt.json').exists() else {}
expected_blocks=64 if variant=='group' else int(variant.split('b')[-1]) if variant.startswith('multi') else 32 if n==64 else n
geometry=report.get('phases',{}).get('prepare',{})
valid=report['status']=='passed' and len(ranks)==n and all(r['status']=='passed' and r['guards']=='passed' and r['padding']=='passed' and r['checked_bytes']==total for r in ranks)
valid=valid and receipt.get('returncode')==0 and receipt.get('remaining_processes')==[] and geometry.get('block_dim')==expected_blocks
fatal=[]
if (suite/'run.log').exists():
    with (suite/'run.log').open(errors='replace') as f:
        for line in f:
            if 'FATAL:' in line or 'WARNFATAL:' in line:
                fatal.append(line.strip()[:400])
                if len(fatal)==2:break
row=dict(runtime_commit=build['runtime_commit'],kernel_sha256=build['kernel']['sha256'],ranks=n,size=size,variant=variant,algorithm=algorithm,block_dim=geometry.get('block_dim'),bytes_per_peer=total//n,total_bytes_per_rank=total,profiling=False,iterations=1,validated=bool(valid),checked_ranks=len(ranks),simulated_us=evidence.get('simulated_us'),error=report.get('error'),failed_phase=report.get('failed_phase'),returncode=receipt.get('returncode'),remaining_processes=receipt.get('remaining_processes'),fatal_lines=fatal,report=str(output/'result.json'))
(output/'summary.json').write_text(json.dumps(row,indent=2));print(json.dumps(row))
assert not receipt.get('remaining_processes'), 'owned process remains'
