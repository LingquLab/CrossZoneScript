"""Stage blue inputs then run the unchanged native T4 object in CPU SST only."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
sys.path.insert(0,str(Path(__file__).parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'alltoall'))
from ssh_transport import run

buffer=io.StringIO()
with contextlib.redirect_stdout(buffer):
    prepared=runpy.run_path(str(Path(__file__).with_name('prepare_native_case.py')))
probe = '''import json
from pathlib import Path
rows=[]
for p in Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  comm=(p/'comm').read_text().strip()
  if comm in ('sst','sst.x','sstsim.x'):rows.append({'pid':int(p.name),'comm':comm})
 except OSError:pass
print(json.dumps({'simulators':rows}))
'''
source='''import json,subprocess
from pathlib import Path
name='tilexr-cpu-b4f4660a-lr8'
case=Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-native-639f2fb3')
container_case='/work/inter-server-native/t4-native-639f2fb3'
info,=json.loads(subprocess.check_output(['docker','inspect',name],timeout=15))
assert info['Id']=='1f98e70b00ed8b171fea3b06152a7fe9507a2270e9dee05c72015af5b995c2f4'
assert not info['State']['Running'] and info['HostConfig']['Runtime']=='runc' and not info['HostConfig']['Devices']
subprocess.run(['docker','start',name],capture_output=True,check=True,timeout=30)
result={'candidate_commit':'639f2fb3d71fd14197b82fb86db2e51eccd49d00',
 'kernel_sha256':'9fa520e775c4586cfe529174bef00975b49342f3ef541bcb658ecaff8273f1c2',
 'case':container_case,'world':64,'tokens':4,'hidden':256,'compiled_in_green':False}
try:
 check='import glob,json; d=glob.glob("/dev/davinci*")+glob.glob("/dev/devmm*")+glob.glob("/dev/hisi_hdc*"); assert not d; print(json.dumps({"npu_devices":d}))'
 p=subprocess.run(['docker','exec',name,'python3','-c',check],capture_output=True,check=True,timeout=15)
 result['cpu_only']=json.loads(p.stdout)
 argv=['python3',container_case+'/adapter/run.py','--case',container_case,
  '--fixture',container_case+'/fixture-data','--checker',container_case+'/source/tools/unitest/simulator/experiments/inter_server_send/fixture.py',
  '--environment',container_case+'/sst-environment.json','--threads','8','--timeout','1800']
 with (case/'native-runner.log').open('wb') as log:
  p=subprocess.run(['docker','exec',name,'bash','-c','source /opt/sim/cann-env.sh; exec "$@"','bash',*argv],stdout=log,stderr=subprocess.STDOUT,timeout=1950)
 result['runner_exit_code']=p.returncode
 if (case/'run.json').is_file():
  record=json.loads((case/'run.json').read_text())
  result['run']={k:v for k,v in record.items() if k in ('status','returncode','error','guards','pid','timeout_seconds')}
 else:result['run']={'status':'not_started_or_unknown'}
except Exception as error:
 result['runner_exception_type']=type(error).__name__
finally:
 probe=PROBE
 p=subprocess.run(['docker','exec',name,'python3','-c',probe],capture_output=True,timeout=20)
 if p.returncode==0:
  result['processes']=json.loads(p.stdout)
  if not result['processes']['simulators']:
   subprocess.run(['docker','stop','--time','10',name],capture_output=True,check=True,timeout=25)
   result['container_stopped']=True
 (case/'task-result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
'''
source='PROBE='+repr(probe)+'\n'+source
result=run(source,timeout=2050)
report=Path('D:/project/inter-server-sst-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
(report/'native-t4-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=True))
if result.get('runner_exit_code')!=0 or result.get('run',{}).get('status')!='sst_and_checker_passed':
    raise SystemExit(1)
