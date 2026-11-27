import base64,concurrent.futures,csv,hashlib,io,json,subprocess,zipfile
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
remote=r'''
import base64,csv,io,json,statistics,zipfile
from pathlib import Path
from collections import defaultdict
root=Path('/root/ubx-mem-group-matched-d23ffa56');receipt=json.loads((root/'receipt.json').read_text());assert receipt['status']=='PASS'
rounds=[];rows=[]
for phase,pattern in [('A1','intra'),('B1','cross'),('B2','cross'),('A2','intra')]:
 groups=defaultdict(list)
 for rank in range(16):
  for s in (root/phase/('rank%d.jsonl'%rank)).read_text().splitlines():
   r=json.loads(s)
   if r['test'] not in ['T01','T02']:continue
   assert r['verified'] and r['records_sent']==128 and r['records_received']==0
   row={k:v for k,v in r.items() if k not in ['profiles','category_records']};row.update(host=host,phase=phase,pattern=pattern);rows.append(row)
   if not r['warmup']:groups[(r['test'],r['round'])].append(r)
 for (test,roundno),rr in sorted(groups.items()):
  assert len(rr)==16;rounds.append(dict(host=host,phase=phase,pattern=pattern,test=test,send_cores=rr[0]['send_cores'],round=roundno,slowest_send_us=max(r['send_us'] for r in rr),records=2048,wire_bytes=8388608))
summary=[]
for pattern in ['intra','cross']:
 for test in ['T01','T02']:
  v=sorted(r['slowest_send_us'] for r in rounds if r['pattern']==pattern and r['test']==test);assert len(v)==20
  summary.append(dict(host=host,pattern=pattern,send_cores=8 if test=='T01' else 16,count=20,mean_us=statistics.mean(v),p95_us=v[18]+(v[19]-v[18])*.05,max_us=max(v)))
payload=io.BytesIO()
with zipfile.ZipFile(payload,'w',zipfile.ZIP_DEFLATED) as z:
 z.write(root/'receipt.json','receipt.json')
 for p in (root/'inputs').iterdir():z.write(p,'inputs/'+p.name)
 z.write(root/'source/dispatch/mem-group-bench/tests/matched_inputs.py','reproduction/matched_inputs.py')
 for phase in ['A1','B1','B2','A2']:
  for name in ['config.json','result.json','groups.bin']:z.write(root/phase/name,'raw/'+phase+'/'+name)
  for rank in range(16):
   f=root/phase/('rank%d.jsonl'%rank);z.write(f,'raw/'+phase+'/'+f.name)
   for line in f.read_text().splitlines():
    r=json.loads(line)
    if r['test'] not in ['T01','T02'] or r['round']!=14:continue
    start=min(c['start'] for c in r['profiles']);events=[dict(name='SEND' if c['core']<r['send_cores'] else 'EMPTY_OBSERVER',ph='X',pid=rank,tid=c['core'],ts=(c['start']-start)/1000,dur=(c['end']-c['start'])/1000,args=dict(records=c['records'])) for c in r['profiles']]
    z.writestr('traces/'+phase+'/'+r['test']+'/rank%d.trace.json'%rank,json.dumps(dict(traceEvents=events,metadata=dict(clock='per-rank relative only; not aligned across cards'))))
 for name,data in [('rank_rounds.csv',rows),('per_round.csv',rounds),('summary.csv',summary)]:
  s=io.StringIO();w=csv.DictWriter(s,fieldnames=list(data[0]));w.writeheader();w.writerows(data);z.writestr('analysis/'+name,s.getvalue())
print(json.dumps(dict(archive_b64=base64.b64encode(payload.getvalue()).decode(),summary=summary)))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input='host='+repr(host)+'\n'+remote,text=True,capture_output=True,timeout=180);Path('matched-export-'+host+'.stderr.log').write_text(p.stderr);assert p.returncode==0;return host,json.loads(p.stdout)
out=Path('D:/work/UBX攻关/UBX_Control/mem-group-8lane-matched-load');out.mkdir(parents=True,exist_ok=True);summary=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,['90.90.93.81','90.90.93.83']))
for host,data in results:
 with zipfile.ZipFile(io.BytesIO(base64.b64decode(data['archive_b64']))) as z:
  for m in z.infolist():
   n=Path(m.filename);assert not n.is_absolute() and '..' not in n.parts
   dest=out/host/n;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(z.read(m))
 summary.extend(data['summary'])
with (out/'summary.csv').open('w',newline='',encoding='utf-8-sig') as f:w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
(out/'README.txt').write_text('等负载、无SELF校准，组内与跨组每源/目的rank均128条4096B Record，各512KiB，每server共2048条8MiB，7个peer，逐边映射目标rank异或8。ABBA：A1组内、B1跨组、B2跨组、A2组内，每次5预热10测量。send-only，不并发接收扫描；发送全部完成后Host汇合再完整校验接收内容。\n主比较仅使用T01/T02（8/16发送核），每个条件20个测量server-round。复用旧Host/kernel，因此raw仍包含T03～T10，它们是过滤/排序重复或空任务，不是本次主对照。原始profiles在JSONL中；trace是最后轮单rank核寿命，不能视为跨卡同步或native msprof。\n这是校准矩阵，不是原真实路由子集，也不对应已确认的同事两组配对/四组互发矩阵。8+8仍为逻辑分组，包含不同4卡连接类型。\n',encoding='utf-8')
entries=[dict(path=p.relative_to(out).as_posix(),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in out.rglob('*') if p.is_file() and p.name!='files.sha256.json'];(out/'files.sha256.json').write_text(json.dumps(entries,indent=2))
archive=out.with_suffix('.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for p in out.rglob('*'):
  if p.is_file():z.write(p,p.relative_to(out).as_posix())
print(json.dumps(dict(archive=str(archive),archive_bytes=archive.stat().st_size,archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),summary=summary),ensure_ascii=False))
