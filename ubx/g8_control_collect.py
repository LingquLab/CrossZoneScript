# 中文：只读取已完成EP32控制对照的summary与原始打点，汇总同卡时钟阶段和不含sleep的Event时延。
# English: Read completed EP32 control A/B summaries and raw profiling; aggregate same-device stages and Event timing excluding sleep.
import concurrent.futures,json,subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()]
assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
remote=r'''
import json,struct,statistics
from pathlib import Path
from collections import defaultdict
root=Path('/root/ubx-g8-control-b212b5f4-r5');data=defaultdict(list)
runs=[(1,'ablation-bs32-mt2-513885c310f5'),(2,'ablation-bs32-mt2-40da242e5208'),(2,'ablation-bs32-mt2-ef3d91a979c1'),(1,'ablation-bs32-mt2-de2d1ddd9d9e')]
for mode,run in runs:
 for d in (root/run).glob('rank-*'):
  for p in (d/'records').glob('*.summary.json'):
   summary=json.loads(p.read_text());assert summary['correctness'] and summary['route_control_mode']==mode
   if summary['warmup']:continue
   stem=str(p)[:-13];scale=1e6/json.loads(Path(stem+'.clock.json').read_text())['cycles_per_second']
   data[(mode,'ALL','event_us')].append(summary['dispatch_event_us'])
   data[(mode,'ALL','dispatch_plus_barrier_us')].append(summary['dispatch_event_us']+summary['barrier_device_us'])
   raw=Path(stem+'.records.bin').read_bytes();h=struct.unpack_from('<8sHHHHHBBI16sQ',raw);pos=48;streams={}
   for _ in range(h[8]):
    lane,n,dropped,flags,size=struct.unpack_from('<IIIIQ',raw,pos);assert dropped==0 and flags==1
    streams[lane&65535]={event:t for t,event,_ in [struct.unpack_from('<QII',raw,pos+24+i*16) for i in range(n)]};pos+=size
   start=min(streams[c][1] for c in range(56));end=max(streams[c][2] for c in range(56))
   data[(mode,'ALL','device_span_us')].append((end-start)*scale)
   for core in range(56):
    role='SEND' if core<16 else 'RECV' if core<32 else 'RELAY' if core<48 else 'INTRA'
    e=streams[core];c=streams[core+56]
    data[(mode,role,'start_from_rank_start_us')].append((e[1]-start)*scale)
    for name,(a,b) in {'run_us':(1,2),'prefix_us':(13,14),'planning_us':(33,34),'control_submit_us':(180,181),'first_control_wait_us':(182,183),'control_retire_us':(184,185)}.items():
     if a in e and b in e:data[(mode,role,name)].append((e[b]-e[a])*scale)
    if 181 in e:data[(mode,role,'control_submitted_from_rank_start_us')].append((e[181]-start)*scale)
    for name,section in {'route_wait_total_us':23,'cq_wait_us':8,'peer_init_us':5,'send_tasks_us':4,'relay_load_us':10}.items():
     a=66+2*section
     if a in c and a+1 in c:data[(mode,role,name)].append((c[a+1]-c[a])*scale)
rows=[]
for (mode,role,metric),v in sorted(data.items()):
 v.sort();rows.append(dict(mode=mode,role=role,metric=metric,count=len(v),mean=statistics.mean(v),median=statistics.median(v),p95=v[min(len(v)-1,int(len(v)*.95))],max=max(v)))
print(json.dumps(rows))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=remote,text=True,capture_output=True,timeout=180)
 Path('collect-'+host+'.stderr.log').write_text(p.stderr)
 assert p.returncode==0,'Read-only profiling collection failed'
 return dict(host=host,rows=json.loads(p.stdout))
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,['90.90.93.83','90.90.93.86']))
Path('g8-control-stage-summary.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results))
