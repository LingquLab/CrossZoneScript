import json,subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
remote=r'''
import concurrent.futures,json,subprocess
from pathlib import Path

def probe(i):
 result={'device':i}
 for kind in ['proc-mem','health']:
  p=subprocess.run(['npu-smi','info','-t',kind,'-i',str(i)],capture_output=True,text=True,timeout=15)
  if kind=='proc-mem':result['query_exit']=p.returncode;result['idle']=p.returncode==0 and 'No process in device.' in p.stdout and 'Process id' not in p.stdout
  else:result['health_exit']=p.returncode;result['health']=p.stdout.strip().splitlines()[-6:];result['healthy']=p.returncode==0 and 'Health Status' in p.stdout and 'OK' in p.stdout and 'Alarm' not in p.stdout and 'Critical' not in p.stdout
 return result
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:cards=list(pool.map(probe,range(16)))
paths=['/usr/local/Ascend/cann/set_env.sh','/usr/local/Ascend/cann-9.2.T101','/root/ubx-dispatch-global-ab-d23ffa56/runtime-install','/root/ubx-mem-relay-d23ffa56']
p=subprocess.run(['npu-smi','info','-l'],capture_output=True,text=True,timeout=20)
print(json.dumps(dict(host='90.90.93.86',cards=cards,ready=all(c['idle'] and c['healthy'] for c in cards),paths={s:Path(s).exists() for s in paths},topology_exit=p.returncode,topology_excerpt=p.stdout.splitlines()[:20])))
'''
p=subprocess.run(ssh+['root@90.90.93.86','python3 -'],input=remote,text=True,capture_output=True,timeout=120)
Path('relay-86-probe.stdout.log').write_text(p.stdout);Path('relay-86-probe.stderr.log').write_text(p.stderr)
assert p.returncode==0,'86 probe failed; inspect saved logs'
print(json.dumps(json.loads(p.stdout)))
