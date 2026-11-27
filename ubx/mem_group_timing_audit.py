import concurrent.futures,json,subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
remote=r'''
import json,statistics
from pathlib import Path
from collections import defaultdict
root=Path('/root/ubx-mem-group-8lane-d23ffa56/formal-3b986c21ff');rows=[]
for rank in range(16):
 for line in (root/('rank%d.jsonl'%rank)).read_text().splitlines():
  r=json.loads(line)
  if r['warmup']:continue
  assert r['verified'];send=r['profiles'][:r['send_cores']];obs=r['profiles'][r['send_cores']:];begin=min(p['start'] for p in r['profiles']);active=[p for p in obs if p['records']]
  row=dict(test=r['test'],rank=rank,repeat=r['repeat'],round=r['round'],send_us=r['send_us'],receive_us=r['receive_us'],send_start_spread_us=(max(p['start'] for p in send)-min(p['start'] for p in send))/1000,observer_start_spread_us=(max(p['start'] for p in obs)-min(p['start'] for p in obs))/1000,all_core_start_spread_us=(max(p['start'] for p in r['profiles'])-begin)/1000,max_sender_duration_us=max(p['end']-p['start'] for p in send)/1000,max_observer_duration_us=max(p['end']-p['start'] for p in obs)/1000,first_observation_us=min(p['first']-begin for p in active)/1000 if active else 0,last_observation_us=max(p['last']-begin for p in active)/1000 if active else 0,observer_polls=sum(p['polls'] for p in obs),records_received=r['records_received'],records_sent=r['records_sent'],observer_first_to_last_mean_us=statistics.mean((p['last']-p['first'])/1000 for p in active) if active else 0)
  row['polls_per_record']=row['observer_polls']/row['records_received'] if row['records_received'] else 0;rows.append(row)
bytest=defaultdict(list);byround=defaultdict(list)
for r in rows:bytest[r['test']].append(r);byround[(r['test'],r['repeat'],r['round'])].append(r)
summary=[]
for test,rr in sorted(bytest.items()):
 metrics={k:statistics.mean(r[k] for r in rr) for k in rr[0] if k not in ['test','rank','repeat','round']}
 metrics.update(test=test,rank_rounds=len(rr),server_slowest_receive_mean_us=statistics.mean(max(r['receive_us'] for r in block) for (t,_,_),block in byround.items() if t==test),server_slowest_send_mean_us=statistics.mean(max(r['send_us'] for r in block) for (t,_,_),block in byround.items() if t==test));summary.append(metrics)
print(json.dumps(dict(summary=summary,scope='read-only existing formal light profiles; sender and receiver directions differ')))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=remote,text=True,capture_output=True,timeout=90)
 Path('timing-audit-'+host+'.stderr.log').write_text(p.stderr);assert p.returncode==0
 r=json.loads(p.stdout);r['host']=host;return r
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for result in pool.map(one,['90.90.93.81','90.90.93.83']):print(json.dumps(result),flush=True)
