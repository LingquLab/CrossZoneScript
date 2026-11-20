"""Run LR8 64 logical ranks, INT32, 128 elements (512B) per peer on CPU SST."""
import json
import os
from pathlib import Path
import subprocess
import sys
from ssh_transport import run

REPORT=Path('D:/project/tilexr-sim-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
UNITEST=Path('D:/project/tileXR-worktrees/sst-b4f4660a-unitest/tools/unitest')
LABEL='unitest-64p-512b'
NAME='tilexr-cpu-b4f4660a-lr8'
CID='1f98e70b00ed8b171fea3b06152a7fe9507a2270e9dee05c72015af5b995c2f4'
assert not (REPORT/LABEL).exists(),'64-rank report exists; inspect before replay'
case=json.loads((UNITEST/'cases/alltoall.json').read_text(encoding='utf-8'))
case.update(id='alltoall-int32-64p-128',elements_per_peer=128)
case_file=REPORT/'lr8-64p-128.case.json'
case_file.write_text(json.dumps(case,indent=2),encoding='utf-8')
config=json.loads((REPORT/'sst-lr8-128.json').read_text(encoding='utf-8'))
config.update(rank_size=64,suite_output='/work/lr8-source/lr8-64p-512b',threads=8,
              simulation_timeout=1800,timeout=2100)
config_file=REPORT/'sst-lr8-64p-128.json'
config_file.write_text(json.dumps(config,indent=2),encoding='utf-8')
start='''import json,subprocess
name='tilexr-cpu-b4f4660a-lr8'
info,=json.loads(subprocess.check_output(['docker','inspect',name],timeout=15))
assert info['Id']=='1f98e70b00ed8b171fea3b06152a7fe9507a2270e9dee05c72015af5b995c2f4'
assert info['HostConfig']['Runtime']=='runc' and not info['HostConfig']['Devices'] and not info['HostConfig']['Privileged']
assert not info['State']['Running'],'Task container already active; inspect ownership first'
subprocess.run(['docker','start',name],capture_output=True,check=True,timeout=30)
p=subprocess.run(['docker','exec',name,'python3','-c',"import glob,json; from pathlib import Path; d=glob.glob('/dev/davinci*')+glob.glob('/dev/devmm*')+glob.glob('/dev/hisi_hdc*'); assert not d; assert not Path('/work/lr8-source/lr8-64p-512b').exists(); print(json.dumps({'npu_devices':d}))"],capture_output=True,check=True,timeout=20)
print(p.stdout.decode())
'''
cpu_check=run(start,timeout=75)
env=os.environ.copy()
env.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
exit_code=None
try:
    with (REPORT/(LABEL+'.log')).open('wb') as log:
        p=subprocess.run([sys.executable,'-m','controller','--case',str(case_file),'--mode','single',
            '--target','sst','--sim-config',str(config_file),'--no-profiling','--output-dir',str(REPORT/LABEL)],
            cwd=UNITEST,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=2250)
    exit_code=p.returncode
finally:
    # Stop only when SST has exited. Preserve an unknown live run for diagnosis.
    inner="import json; from pathlib import Path; rows=[]\nfor p in Path('/proc').iterdir():\n if not p.name.isdigit(): continue\n try:\n  c=(p/'comm').read_text().strip()\n  if c in ('sst','sst.x','sstsim.x'): rows.append({'pid':int(p.name),'comm':c})\n except OSError: pass\nprint(json.dumps({'simulators':rows}))"
    finish='import json,subprocess\np=subprocess.run('+repr(['docker','exec',NAME,'python3','-c',inner])+',capture_output=True,check=True,timeout=20)\nr=json.loads(p.stdout)\nif not r["simulators"]: subprocess.run(["docker","stop","--time","10",'+repr(NAME)+'],capture_output=True,check=True,timeout=25)\nr["container_stopped"]=not r["simulators"]\nprint(json.dumps(r))'
    cleanup=run(finish,timeout=60)
    (REPORT/'64p-container-state.json').write_text(json.dumps(cleanup,indent=2),encoding='utf-8')
assert exit_code==0,'64-rank UniTest failed; inspect retained '+str(REPORT/LABEL)
result=json.loads((REPORT/LABEL/'result.json').read_text(encoding='utf-8'))
assert result['status']=='passed' and result['operator_result']['status']=='passed'
assert cleanup['container_stopped'],'Simulator still active; inspect state'
print(json.dumps({'rank_size':64,'elements_per_peer':128,'bytes_per_peer':512,
    'bytes_per_rank':32768,'total_input_bytes':2097152,'status':result['status'],
    'operator_result':result['operator_result'],'report':str(REPORT/LABEL),
    'cpu_only_check':cpu_check,'cleanup':cleanup},ensure_ascii=True))
