"""Build LR8 and run the documented ABI and environment phases."""
import json
import os
from pathlib import Path
import subprocess
import sys

REPORT=Path('D:/project/tilexr-sim-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
TOOL=Path('D:/project/tileXR-worktrees/sst-b4f4660a-tool')
source=json.loads((REPORT/'source/result.json').read_text(encoding='utf-8'))
target=json.loads((REPORT/'target.json').read_text(encoding='utf-8'))
container=json.loads((REPORT/'container.json').read_text(encoding='utf-8'))
hashes={row['path']:row['sha256'] for row in container['cpu_only_inspection']['paths']}
common=['--inventory',str(REPORT/'inventory.json')]
for key in ('cluster','host','hostname','container','container_id','image_id'):
    common += ['--'+key.replace('_','-'),target[key]]
common += ['--sources',source['sources'],'--output','/work/lr8-source/kernel',
    '--runtime-commit',source['runtime_commit'],'--source-lock-sha256',source['source_lock_sha256'],
    '--cann-root','/opt/sim/Ascend/cann-9.1.0','--driver-root','/usr/local/Ascend/driver',
    '--cann-env','/opt/sim/cann-env.sh','--cann-env-sha256',hashes['/opt/sim/cann-env.sh'],
    '--ubus-library','/opt/sim/plugins/libUBusSimulator.so','--ubus-sha256',hashes['/opt/sim/plugins/libUBusSimulator.so'],
    '--sim-variant','sim-split1']
phases=[('build',['--kernel-name','urma_lr8','--stage-profiling','0'],'verified_not_simulated'),
        ('abi',[],'host_abi_verified_not_simulated'),
        ('inspect',['--library-dir','/opt/sim/plugins','--inspection-output','/work/lr8-source/environment'],
         'resources_verified_not_simulated')]
env=os.environ.copy()
env.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
summary=[]
for mode,extra,expected in phases:
    argv=[sys.executable,str(TOOL/'scripts/run_alltoall_sim_kernel.py'),mode,*common,*extra,
          '--timeout','600','--local-output',str(REPORT/mode)]
    with (REPORT/(mode+'.log')).open('wb') as log:
        proc=subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,env=env,timeout=650)
    assert proc.returncode==0, mode+' failed; inspect '+str(REPORT/(mode+'.log'))
    result=json.loads((REPORT/mode/'result.json').read_text(encoding='utf-8'))
    assert result['state']==expected, mode+' returned unexpected state'
    summary.append({'phase':mode,'state':result['state']})
print(json.dumps({'phases':summary,'kernel':'urma_lr8','variant':'sim-split1',
                  'stage_profiling':False,'sst_executed':False,'report':str(REPORT)},ensure_ascii=True))
