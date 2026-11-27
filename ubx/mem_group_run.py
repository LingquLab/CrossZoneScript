import concurrent.futures,json,shlex,subprocess,uuid
from pathlib import Path
settings=json.loads((Path(__file__).resolve().parents[2]/'args.json').read_text(encoding='utf-8-sig'))
phase=settings.get('phase','smoke');assert phase in ['smoke','formal','detail']
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
run_id=phase+'-'+uuid.uuid4().hex[:10]
remote=r'''
import hashlib,json,shlex,subprocess
from pathlib import Path
root=Path('/root/ubx-mem-group-8lane-d23ffa56');receipt=json.loads((root/'build-receipt.json').read_text());assert receipt['revision']=='1a3787a6fe5872e86bb8ba79ec58d3c87215a7c1' and all(s['exit']==0 for s in receipt['stages'])
for n,digest in receipt['artifacts'].items():assert hashlib.sha256((root/'build'/n).read_bytes()).hexdigest()==digest
if phase!='smoke':
 prior=json.loads((root/'smoke-verified.json').read_text());assert prior['revision']==receipt['revision'] and prior['passed']
reps,warmup,measured,detail={'smoke':(1,1,2,0),'formal':(3,10,20,0),'detail':(1,2,5,1)}[phase]
source=root/'source/dispatch/mem-group-bench';output=root/run_id
cmd=['python3',str(source/'run.py'),'--build',str(root/'build'),'--runtime','/root/ubx-dispatch-global-ab-d23ffa56/runtime-install','--routes',str(root/'inputs/expert_ids.bin'),'--output',str(output),'--port',str(port),'--repetitions',str(reps),'--warmup',str(warmup),'--measured',str(measured),'--detail',str(detail)]
p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=1400)
(root/(run_id+'.stdout.log')).write_text(p.stdout);(root/(run_id+'.stderr.log')).write_text(p.stderr)
result=dict(phase=phase,run_id=run_id,output=str(output),exit=p.returncode,revision=receipt['revision'])
if p.returncode==0:
 data=json.loads(p.stdout);assert data['status']=='PASS';result.update(data)
 if phase=='smoke':(root/'smoke-verified.json').write_text(json.dumps(dict(revision=receipt['revision'],passed=True,run_id=run_id)))
else:
 result['status']='FAIL';result['runner_tail']=p.stderr.splitlines()[-5:]
 if output.exists():result['worker_error_tails']=[dict(rank=n,tail=(output/('rank%d.log'%n)).read_text(errors='replace').splitlines()[-4:]) for n in range(16) if (output/('rank%d.log'%n)).is_file()][:4]
print(json.dumps(result))
'''
def one(item):
 index,host=item;script='phase='+repr(phase)+'\nrun_id='+repr(run_id)+'\nport='+str(31000+index*100+int(uuid.uuid4().hex[:3],16)%80)+'\n'+remote
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=script,text=True,capture_output=True,timeout=1480)
 Path('run-'+host+'.stdout.log').write_text(p.stdout);Path('run-'+host+'.stderr.log').write_text(p.stderr);assert p.returncode==0,'SSH run '+host
 result=json.loads(p.stdout);result['host']=host;return result
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,enumerate(['90.90.93.81','90.90.93.83'])))
for result in results:print(json.dumps(result),flush=True)
assert all(r['exit']==0 and r['status']=='PASS' for r in results),'Test failed; inspect saved logs before retry'
