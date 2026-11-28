import concurrent.futures,json,subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
remote=r'''
import json,struct,statistics
from pathlib import Path
from collections import defaultdict
root=Path('/root/ubx-rdma-group8-overlap-d23ffa56');data=defaultdict(list)
for variant,run in [('control','ablation-bs32-mt2-089c47c70dc2'),('group8','ablation-bs32-mt2-307300a404ba')]:
 for d in (root/run).glob('rank-*'):
  rank=int(d.name.split('-')[1]);group=rank%16//8
  for p in (d/'records').glob('*.summary.json'):
   summary=json.loads(p.read_text());assert summary['correctness']
   if summary['warmup']:continue
   stem=str(p)[:-13];freq=json.loads(Path(stem+'.clock.json').read_text())['cycles_per_second'];scale=1e6/freq
   raw=Path(stem+'.records.bin').read_bytes();h=struct.unpack_from('<8sHHHHHBBI16sQ',raw);pos=48;streams={}
   for _ in range(h[8]):
    lane,n,dropped,flags,size=struct.unpack_from('<IIIIQ',raw,pos);assert dropped==0 and flags==1
    streams[lane&65535]={e:t for t,e,_ in [struct.unpack_from('<QII',raw,pos+24+i*16) for i in range(n)]};pos+=size
   for core in range(56):
    role='SEND' if core<16 else 'RECV' if core<32 else 'RELAY' if core<48 else 'INTRA'
    e=streams[core];c=streams[core+56]
    metrics={'run':(1,2),'planning':(33,34),'relay_listen':(35,36),'forward_span':(54,55),'send_schedule':(29,30)}
    for name,(a,b) in metrics.items():
     if a in e and b in e and e[b]>=e[a]:data[(variant,group,role,name)].append((e[b]-e[a])*scale)
    if role=='RELAY':
     for name,event in [('plan_end_from_start',34),('first_arrival_from_start',53),('first_forward_from_start',54),('last_arrival_from_start',64),('last_forward_from_start',55)]:
      if event in e:data[(variant,group,role,name)].append((e[event]-e[1])*scale)
    names={4:'send_tasks',5:'peer_init',6:'encode',7:'publish',8:'cq_wait',9:'retire',10:'relay_load',11:'relay_tasks',12:'relay_positions',13:'relay_export',14:'listen_load',15:'listen_probe',17:'mem_load',19:'mem_pack',20:'mem_publish',21:'mem_wait'}
    for section,name in names.items():
     event=66+2*section
     if event in c and event+1 in c:data[(variant,group,role,name)].append((c[event+1]-c[event])*scale)
rows=[]
for (variant,group,role,metric),values in sorted(data.items()):
 if role not in ['SEND','RELAY'] and metric!='run':continue
 rows.append(dict(variant=variant,group=group,role=role,metric=metric,count=len(values),mean_us=statistics.mean(values),max_us=max(values)))
print(json.dumps(dict(rows=rows)))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=remote,text=True,capture_output=True,timeout=150)
 if p.returncode:print(json.dumps(dict(host=host,error=p.stderr[-1500:])))
 assert p.returncode==0;r=json.loads(p.stdout);r['host']=host;return r
results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for r in pool.map(one,['90.90.93.83','90.90.93.86']):results.append(r);print(json.dumps(r))
out=Path('D:/work/UBX攻关/UBX_Control/tilexr-ep32-rdma-group8-overlap-stats/relay-stage-audit.json');out.write_text(json.dumps(results,indent=2),encoding='utf-8')
