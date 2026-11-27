import concurrent.futures,json,subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
remote=r'''
import concurrent.futures,hashlib,json,subprocess
from pathlib import Path
root=Path('/root/ubx-token-merge-matrix-d23ffa56')
def probe(device):
 p=subprocess.run(['npu-smi','info','-t','proc-mem','-i',str(device)],capture_output=True,text=True,timeout=15)
 h=subprocess.run(['npu-smi','info','-t','health','-i',str(device)],capture_output=True,text=True,timeout=15)
 return dict(device=device,idle=p.returncode==0 and 'No process in device.' in p.stdout and 'Process id' not in p.stdout,health_exit=h.returncode,health=h.stdout.strip().splitlines()[-4:])
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:cards=list(pool.map(probe,range(16)))
faults=[]
for d in [0,1]:
 p=subprocess.run(['npu-smi','info','-t','current-fault-event','-i',str(d)],capture_output=True,text=True,timeout=15);faults.append(dict(device=d,exit=p.returncode,tail=(p.stdout+p.stderr).splitlines()[-10:]))
hello=dict(status='SKIPPED')
source=Path('/root/ubx-control-ab-ep16-d23ffa56/official-hello');demo=source/'build/demo'
if cards[1]['idle'] and demo.is_file():
 assert 'aclrtSetDevice(1)' in (source/'hello_world.asc').read_text()
 try:
  p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+str(demo)],capture_output=True,text=True,timeout=25)
  (root/'hello-recheck.stdout.log').write_text(p.stdout);(root/'hello-recheck.stderr.log').write_text(p.stderr)
  hello=dict(status='PASS' if p.returncode==0 and p.stdout.count('Hello World')==8 else 'FAIL',exit=p.returncode,hello_count=p.stdout.count('Hello World'),stderr_tail=p.stderr.splitlines()[-3:])
 except subprocess.TimeoutExpired:hello=dict(status='TIMEOUT',timeout_seconds=25)
 hello['binary_sha256']=hashlib.sha256(demo.read_bytes()).hexdigest()
result=dict(cards=cards,faults=faults,hello=hello);(root/'basic-check.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=remote,text=True,capture_output=True,timeout=150);Path('basic-check-'+host+'.stderr.log').write_text(p.stderr);assert p.returncode==0;r=json.loads(p.stdout);r['host']=host;return r
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for r in pool.map(one,['90.90.93.81','90.90.93.83']):print(json.dumps(r),flush=True)
