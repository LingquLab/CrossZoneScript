import concurrent.futures,json,subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
remote=r'''
import concurrent.futures,json,subprocess

def probe(i):
 result={'device':i}
 for name,kind in [('occupancy','proc-mem'),('health','health')]:
  p=subprocess.run(['npu-smi','info','-t',kind,'-i',str(i)],capture_output=True,text=True,timeout=15)
  if name=='occupancy':result['idle']=p.returncode==0 and 'No process in device.' in p.stdout and 'Process id' not in p.stdout
  else:result['health_exit']=p.returncode;result['health']=p.stdout.strip().splitlines()[-6:];result['healthy']=p.returncode==0 and 'Alarm' not in p.stdout and 'OK' in p.stdout
 return result
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:cards=list(pool.map(probe,range(16)))
print(json.dumps(dict(cards=cards,ready=all(c['idle'] and c['healthy'] for c in cards))))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=remote,text=True,capture_output=True,timeout=100);assert p.returncode==0
 r=json.loads(p.stdout);r['host']=host;return r
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for r in pool.map(one,['90.90.93.81','90.90.93.83']):print(json.dumps(r),flush=True)
