import concurrent.futures,json,subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
remote=r'''
import json,statistics
from pathlib import Path
from collections import defaultdict
root=Path('/root/ubx-mem-group-8lane-d23ffa56/formal-3b986c21ff');assert json.loads((root/'result.json').read_text())['status']=='PASS';groups=defaultdict(list)
for rank in range(16):
 rows=[json.loads(s) for s in (root/('rank%d.jsonl'%rank)).read_text().splitlines()];assert len(rows)==450
 for r in rows:
  assert r['verified']
  if not r['warmup']:groups[(r['test'],r['repeat'],r['round'])].append(r)
bytest=defaultdict(list)
for (test,repeat,roundno),rows in groups.items():
 assert len(rows)==16
 counts=[sum(r['category_records'][i] for r in rows) for i in range(3)]
 bytest[test].append(dict(receive=max(r['receive_us'] for r in rows),send=max(r['send_us'] for r in rows),counts=counts))
summary=[]
for test,rows in sorted(bytest.items()):
 v=sorted(r['receive'] for r in rows);assert len(v)==30;pos=(len(v)-1)*.95;lo=int(pos);hi=min(lo+1,len(v)-1)
 summary.append(dict(test=test,count=len(v),mean_receive_us=statistics.mean(v),p95_receive_us=v[lo]+(v[hi]-v[lo])*(pos-lo),max_receive_us=max(v),mean_send_us=statistics.mean(r['send'] for r in rows),category_records=rows[0]['counts']))
print(json.dumps(dict(formal='formal-3b986c21ff',summary=summary)))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=remote,text=True,capture_output=True,timeout=90)
 Path('formal-summary-'+host+'.stderr.log').write_text(p.stderr);assert p.returncode==0
 r=json.loads(p.stdout);r['host']=host;return r
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for r in pool.map(one,['90.90.93.81','90.90.93.83']):print(json.dumps(r),flush=True)
