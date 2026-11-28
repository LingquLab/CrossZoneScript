import concurrent.futures,json,subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
remote=r'''
import json,struct,statistics
from pathlib import Path
root=Path('/root/ubx-rdma-group8-d23ffa56'); rows=[]
for variant,run in [('control','ablation-bs32-mt2-d59d86ac98e9'),('group8','ablation-bs32-mt2-bffe1ea85d15')]:
 for d in sorted((root/run).glob('rank-*')):
  for p in sorted((d/'records').glob('*.summary.json')):
   s=json.loads(p.read_text());assert s['correctness']
   if s['warmup']:continue
   stem=str(p)[:-len('.summary.json')];clock=json.loads(Path(stem+'.clock.json').read_text());freq=clock['cycles_per_second'];assert freq>0
   data=Path(stem+'.records.bin').read_bytes();h=struct.unpack_from('<8sHHHHHBBI16sQ',data);assert h[1:7]==(1,0,48,16,24,1) and h[-1]==len(data)
   offset=48;intervals=[]
   for _ in range(h[8]):
    lane,n,dropped,flags,size=struct.unpack_from('<IIIIQ',data,offset);assert dropped==0 and flags==1 and size==24+n*16
    records=[struct.unpack_from('<QII',data,offset+24+i*16) for i in range(n)]
    if (lane&65535)<56:
     a=[t for t,e,r in records if e==1];b=[t for t,e,r in records if e==2];assert len(a)==len(b)==1 and b[0]>a[0];intervals.append((a[0],b[0]))
    offset+=size
   assert len(intervals)==56 and offset==len(data)
   scale=1e6/freq
   rows.append(dict(variant=variant,rank=s['rank'],round=s['round'],event_us=s['dispatch_event_us'],max_core_us=max(b-a for a,b in intervals)*scale,core_span_us=(max(b for a,b in intervals)-min(a for a,b in intervals))*scale,frequency=freq,rdma_bytes=s['rdma_data_bytes'],wqes=s['data_wqes'],mem_records=s['mem_records']))
assert len(rows)==320
print(json.dumps({'rows':rows}))
'''
def read(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=remote,text=True,capture_output=True,timeout=120)
 assert p.returncode==0,host+': '+p.stderr[-1500:]
 return host,json.loads(p.stdout)['rows']
rows=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for host,items in pool.map(read,['90.90.93.83','90.90.93.86']):
  for r in items:r['host']=host;rows.append(r)
import statistics,csv
def stats(v):
 v=sorted(v);z=(len(v)-1)*.95;i=int(z)
 return dict(count=len(v),mean_us=statistics.mean(v),p95_us=v[i]+(v[min(i+1,len(v)-1)]-v[i])*(z-i),max_us=max(v))
summary=[]
for variant in ['control','group8']:
 rr=[r for r in rows if r['variant']==variant];assert len(rr)==320 and len({(r['rank'],r['round']) for r in rr})==320
 for metric in ['event_us','max_core_us','core_span_us','rdma_bytes','wqes','mem_records']:
  summary.append(dict(variant=variant,metric=metric,aggregation='rank_round',**stats([r[metric] for r in rr])))
  summary.append(dict(variant=variant,metric=metric,aggregation='per_round_slowest_rank',**stats([max(r[metric] for r in rr if r['round']==n) for n in sorted({r['round'] for r in rr})])))
out=Path('D:/work/UBX攻关/UBX_Control/tilexr-ep32-rdma-group8-stats');out.mkdir(parents=True,exist_ok=True)
(out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
with (out/'measured-rank-rounds.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
print(json.dumps(dict(directory=str(out),frequencies=sorted({r['frequency'] for r in rows}),summary=summary)))
