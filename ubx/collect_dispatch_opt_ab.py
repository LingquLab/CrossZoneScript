import concurrent.futures, csv, hashlib, json, statistics, subprocess, zipfile
from pathlib import Path

RUNS=[('A1','control','ablation-bs32-mt2-cc751a350605'),('B1','global','ablation-bs32-mt2-4464340ddc82'),('B2','global','ablation-bs32-mt2-3bf30e322d36'),('A2','control','ablation-bs32-mt2-f4b84cfc5eb9')]
REMOTE=r'''
import json,statistics
from pathlib import Path
runs=RUNS
root=Path('/root/ubx-dispatch-global-ab-d23ffa56')
rows=[];stages=[];locations=[]
for phase,variant,run in runs:
 case=root/variant/run/'output'
 files=sorted(case.glob('rank*/rounds.jsonl'))
 assert len(files)==16,(str(case),len(files))
 groups={}
 for f in files:
  records=[json.loads(s) for s in f.read_text().splitlines() if s.strip()]
  assert len(records)==30
  for r in records:
   assert r['verified'] and r['flags_epoch_verified'] and len(r['cores'])==56
   assert r['profile_mode']=='full' and not r['clock_aligned']
   if variant=='global': assert r['global_routes_verified'] and r['route_qps']==0
   cores=[c['profile'] for c in r['cores']]; scale=1e6/r['tick_hz']
   assert all(c['status']==0 and c['start']<=c['end'] for c in cores)
   rows.append(dict(phase=phase,variant=variant,run_id=run,rank=r['rank'],round=r['round'],warmup=r['warmup'],dispatch_kernel_us=r['dispatch_kernel_us'],device_core_span_us=(max(c['end'] for c in cores)-min(c['start'] for c in cores))*scale,max_core_duration_us=max(c['end']-c['start'] for c in cores)*scale,verified=True))
  trace=f.parent/'dispatch.last.trace.json'
  obj=json.loads(trace.read_text());events=obj['traceEvents'] if isinstance(obj,dict) else obj
  route=[]
  for e in events:
   if e.get('ph')!='X' or 'dur' not in e:continue
   tid=int(e['tid']);role='SEND' if tid<16 else 'INTRA' if tid<24 else 'RELAY' if tid<40 else 'RECV'
   groups.setdefault((role,e['name']),[]).append(e['dur'])
   if e['name']=='route_sync':route.append(e)
  if route:groups.setdefault(('RANK','route_span'),[]).append(max(e['ts']+e['dur'] for e in route)-min(e['ts'] for e in route))
  locations.append(dict(phase=phase,variant=variant,run_id=run,rank=records[0]['rank'],rounds_jsonl=str(f),trace_json=str(trace),events_bin=str(f.parent/'events.bin')))
 for (role,stage),v in groups.items():
  v.sort();stages.append(dict(phase=phase,variant=variant,run_id=run,role=role,stage=stage,count=len(v),mean_us=statistics.mean(v),median_us=statistics.median(v),max_us=max(v),scope='last measured round 29'))
print(json.dumps(dict(rows=rows,stages=stages,locations=locations)))
'''

def main():
 keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.bak','.old'] and b'PRIVATE KEY' in p.read_bytes()]
 assert keys
 out=Path('D:/work/UBX攻关/UBX_Control/dispatch-opt-3094325-control-vs-global-ep32-bs32-profiling');out.mkdir(parents=True,exist_ok=True)
 def collect(host):
  args=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
  for k in keys:args+=['-i',str(k)]
  p=subprocess.run(args+['root@'+host,'python3 -'],input=REMOTE.replace('runs=RUNS','runs='+repr(RUNS)),capture_output=True,text=True,timeout=180)
  (Path.cwd()/('collect-'+host+'.stderr.log')).write_text(p.stderr)
  assert p.returncode==0,host
  data=json.loads(p.stdout)
  for key in data:
   for row in data[key]:row['host']=host
  return data
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:data=list(pool.map(collect,['90.90.93.81','90.90.93.83']))
 rows=[r for d in data for r in d['rows']];measured=[r for r in rows if not r['warmup']]
 assert len(rows)==3840 and len(measured)==2560
 def writecsv(name,records):
  with (out/name).open('w',newline='',encoding='utf-8-sig') as f:
   w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
 def stats(v):
  v=sorted(v);pos=(len(v)-1)*.95;i=int(pos);p95=v[i]+(v[min(i+1,len(v)-1)]-v[i])*(pos-i)
  return dict(count=len(v),mean_us=statistics.mean(v),median_us=statistics.median(v),p95_us=p95,min_us=min(v),max_us=max(v))
 summaries=[];perround=[]
 for phase,variant,run in RUNS:
  rr=[r for r in measured if r['phase']==phase]
  for n in range(10,30):
   samples=[r for r in rr if r['round']==n];assert len(samples)==32
   perround.append(dict(phase=phase,variant=variant,run_id=run,round=n,slowest_rank_us=max(r['dispatch_kernel_us'] for r in samples),mean_rank_us=statistics.mean(r['dispatch_kernel_us'] for r in samples)))
 for variant in ['control','global']:
  for phase in ['pooled']+[p for p,v,_ in RUNS if v==variant]:
   selected=[r for r in measured if r['variant']==variant and (phase=='pooled' or r['phase']==phase)]
   for metric in ['dispatch_kernel_us','device_core_span_us','max_core_duration_us']:
    summaries.append(dict(variant=variant,phase=phase,aggregation='rank_round_samples',metric=metric,**stats([r[metric] for r in selected])))
   selected_rounds=[r for r in perround if r['variant']==variant and (phase=='pooled' or r['phase']==phase)]
   summaries.append(dict(variant=variant,phase=phase,aggregation='per_round_slowest_rank',metric='dispatch_kernel_us',**stats([r['slowest_rank_us'] for r in selected_rounds])))
 writecsv('all_rounds.csv',rows);writecsv('measured_rounds.csv',measured);writecsv('latency_summary.csv',summaries);writecsv('per_round_summary.csv',perround)
 writecsv('profiling_stages.csv',[r for d in data for r in d['stages']]);writecsv('raw_profiling_locations.csv',[r for d in data for r in d['locations']])
 metadata=dict(baseline_revision='771715cc8e2dd617a092246d8786f0576bb7fa81',global_revision='300d2fab3982d935fcba68b1d3b536e1445ec2e8',runtime_revision='0797410beab1da40800611affc9c512cd42525ba',route_sha256='2a58c479564c82f5185d42bfa3608a5e9f2128e0125c921a1d6ab4ec18f63b24',ep=32,batch=32,experts=384,topk=8,warmup_rounds=10,measured_rounds=20,profiling='full internal profiling; not native msprof',clock_aligned=False,runs=RUNS,excluded_startup_run='ablation-bs32-mt2-5543cde6ad39; set device 507033 before dispatch',summary=summaries)
 (out/'experiment.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
 (out/'README.txt').write_text('单位均为微秒。每次实验10轮预热+20轮测量，顺序A1/B1/B2/A2，补跑前存在资源等待。每种版本1280个rank-round测量样本；各rank不视为独立重复实验。dispatch_kernel_us为ACL event计时；device_core_span_us为同卡56核最晚end减最早start，不跨卡比较绝对时间。profiling_stages仅来自每次实验最后一轮29，不代表20轮统计。RELAY route_relay_load_issue是发起阶段，不代表DMA完成。global版本提前上传所有路由，上传开销不计入kernel。全程full profiling，未使用native msprof，未对齐跨机设备时钟。mt2仅为沿用的运行目录标签。启动失败的尝试未计入。原始profiling仍在两台服务器，位置见CSV。\n',encoding='utf-8')
 archive=out.with_suffix('.zip')
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
  for p in sorted(out.iterdir()):z.write(p,p.name)
 print(json.dumps(dict(directory=str(out),archive=str(archive),archive_bytes=archive.stat().st_size,archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),all_samples=len(rows),measured_samples=len(measured),summary=summaries),ensure_ascii=False))
if __name__=='__main__':main()
