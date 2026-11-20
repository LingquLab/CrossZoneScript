"""Run only the previously unexecuted 512B/peer case; retain the failed 516B case."""
import json
import os
from pathlib import Path
import subprocess
import sys

REPORT=Path('D:/project/tilexr-sim-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
UNITEST=Path('D:/project/tileXR-worktrees/sst-b4f4660a-unitest/tools/unitest')
case=json.loads((UNITEST/'cases/alltoall.json').read_text(encoding='utf-8'))
case.update(id='alltoall-int32-128',elements_per_peer=128)
case_file=REPORT/'lr8-128.case.json'
case_file.write_text(json.dumps(case,indent=2),encoding='utf-8')
config=json.loads((REPORT/'sst-lr8-129.json').read_text(encoding='utf-8'))
config['suite_output']='/work/lr8-source/lr8-8p-512b'
config_file=REPORT/'sst-lr8-128.json'
config_file.write_text(json.dumps(config,indent=2),encoding='utf-8')
assert not (REPORT/'unitest-128').exists(),'Aligned report already exists; inspect before continuing'
env=os.environ.copy()
env.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
with (REPORT/'unitest-128.log').open('wb') as log:
    p=subprocess.run([sys.executable,'-m','controller','--case',str(case_file),'--mode','single',
        '--target','sst','--sim-config',str(config_file),'--no-profiling','--output-dir',str(REPORT/'unitest-128')],
        cwd=UNITEST,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1100)
assert p.returncode==0,'Aligned SST case failed; inspect retained report'
result=json.loads((REPORT/'unitest-128/result.json').read_text(encoding='utf-8'))
assert result['status']=='passed' and result['operator_result']['status']=='passed'
print(json.dumps({'rank_size':8,'elements_per_peer':128,'bytes_per_peer':512,
    'status':result['status'],'operator_result':result['operator_result'],
    'report':str(REPORT/'unitest-128'),'npu_used':False},ensure_ascii=True))
