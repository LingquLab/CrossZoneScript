"""Reproduce LR8 non-aligned and aligned 8-rank inputs through pinned UniTest."""
import json
import os
from pathlib import Path
import subprocess
import sys

REPORT=Path('D:/project/tilexr-sim-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
REPO=Path('D:/project/tileXR')
GUIDE='22c95063d09d63d2d87a10525526a4c603f208bd'
TREE=Path('D:/project/tileXR-worktrees/sst-b4f4660a-unitest')
env=os.environ.copy()
env.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1')


def execute(argv,label,timeout=120,cwd=None):
    with (REPORT/(label+'.log')).open('wb') as log:
        proc=subprocess.run([str(x) for x in argv],cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
    assert proc.returncode==0,label+' failed; inspect '+str(REPORT/(label+'.log'))


p=subprocess.run(['git','-C',str(REPO),'cat-file','-e',GUIDE+'^{commit}'],capture_output=True,timeout=10)
if p.returncode:
    execute(['git','-C',REPO,'fetch','origin',GUIDE],'fetch-unitest',180)
assert not TREE.exists(),'UniTest worktree already exists; inspect before continuing'
execute(['git','-C',REPO,'worktree','add','--detach',TREE,GUIDE],'worktree-unitest')
request=json.loads((REPORT/'build/request.json').read_text(encoding='utf-8'))
request['environment_report']='/work/lr8-source/environment'
request_file=REPORT/'current-request.json'
request_file.write_text(json.dumps(request,indent=2),encoding='utf-8')
unitest=TREE/'tools/unitest'
base_case=json.loads((unitest/'cases/alltoall.json').read_text(encoding='utf-8'))
summary=[]
for elements in (129,128):
    case=dict(base_case)
    case.update(id='alltoall-int32-'+str(elements),elements_per_peer=elements)
    case_file=REPORT/('lr8-'+str(elements)+'.case.json')
    case_file.write_text(json.dumps(case,indent=2),encoding='utf-8')
    config={'schema':'tilexr.unitest.sst.v1','tool_checkout':'D:/project/tileXR-worktrees/sst-b4f4660a-tool',
        'tool_commit':'96581a23de5e7e87ce90c770fa130a596600944e','request_file':str(request_file),
        'inventory':str(REPORT/'inventory.json'),'build_report':str(REPORT/'build'),'abi_report':str(REPORT/'abi'),
        'rank_size':8,'suite_output':'/work/lr8-source/lr8-8p-'+str(elements*4)+'b',
        'threads':8,'simulation_timeout':600,'timeout':900}
    config_file=REPORT/('sst-lr8-'+str(elements)+'.json')
    config_file.write_text(json.dumps(config,indent=2),encoding='utf-8')
    label='unitest-'+str(elements)
    execute([sys.executable,'-m','controller','--case',case_file,'--mode','single','--target','sst',
             '--sim-config',config_file,'--no-profiling','--output-dir',REPORT/label],label,1100,unitest)
    result=json.loads((REPORT/label/'result.json').read_text(encoding='utf-8'))
    assert result['status']=='passed' and result['operator_result']['status']=='passed'
    summary.append({'elements_per_peer':elements,'bytes_per_peer':elements*4,'rank_size':8,
                    'status':result['status'],'operator_status':result['operator_result']['status'],
                    'suite_output':config['suite_output'],'report':str(REPORT/label)})
print(json.dumps({'cases':summary,'unitest_commit':GUIDE,'npu_used':False},ensure_ascii=True))
