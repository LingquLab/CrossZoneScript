import base64,concurrent.futures,csv,hashlib,io,json,subprocess,zipfile
from pathlib import Path
run=json.loads((Path(__file__).resolve().parents[2]/'args.json').read_text(encoding='utf-8-sig'))['run'];assert run.startswith('repeat-all-') and '/' not in run
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
remote=r'''
import base64,io,json,zipfile
from pathlib import Path
root=Path('/root/ubx-mem-group-32core-d23ffa56')/run;receipt=json.loads((root/'repeat-receipt.json').read_text());assert receipt['status']=='PASS' and len(receipt['results'])==6
payload=io.BytesIO()
with zipfile.ZipFile(payload,'w',zipfile.ZIP_DEFLATED) as z:
 z.write(root/'repeat-receipt.json','repeat-receipt.json')
 for item in receipt['results']:
  case=Path(item['directory']);assert case.parent==root and json.loads((case/'result.json').read_text())['status']=='PASS'
  for name in ['config.json','result.json','expert_ids.bin','groups.bin']:z.write(case/name,'raw/'+case.name+'/'+name)
  for rank in range(16):
   f=case/('rank%d.jsonl'%rank);z.write(f,'raw/'+case.name+'/'+f.name)
   for line in f.read_text().splitlines():
    r=json.loads(line)
    if r['round']!=14:continue
    if item['override']==32 and int(r['test'][1:])%2==0:continue
    if item['pattern']!='original' and r['test'] not in ['T01','T02']:continue
    start=min(c['start'] for c in r['profiles']);events=[dict(name='SEND' if c['core']<r['send_cores'] else 'EMPTY_OBSERVER',ph='X',pid=rank,tid=c['core'],ts=(c['start']-start)/1000,dur=(c['end']-c['start'])/1000,args=dict(records=c['records'])) for c in r['profiles']]
    z.writestr('traces/'+case.name+'/'+r['test']+'/rank%d.trace.json'%rank,json.dumps(dict(traceEvents=events,metadata=dict(clock='rank local only; not aligned across cards'))))
 for name in ['kernel/bench.cpp','host/main.cpp','include/bench.h','include/plan.h','run.py','CMakeLists.txt']:
  z.write(root.parent/'source/dispatch/mem-group-bench'/name,'source/'+name)
assert len(payload.getvalue())<32*1024*1024
print(json.dumps(dict(archive_b64=base64.b64encode(payload.getvalue()).decode(),receipt=receipt)))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input='run='+repr(run)+'\n'+remote,capture_output=True,text=True,timeout=240);Path('all-export-'+host+'.stderr.log').write_text(p.stderr);assert p.returncode==0;return host,json.loads(p.stdout)
out=Path('D:/work/UBX攻关/UBX_Control')/('mem-group-'+run);out.mkdir(parents=True,exist_ok=True);rows=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,['90.90.93.81','90.90.93.83']))
for host,data in results:
 with zipfile.ZipFile(io.BytesIO(base64.b64decode(data['archive_b64']))) as z:
  for m in z.infolist():
   n=Path(m.filename);assert not n.is_absolute() and '..' not in n.parts
   dest=out/host/n;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(z.read(m))
 for item in data['receipt']['results']:
  for s in item['summary']:rows.append(dict(host=host,pattern=item['pattern'],override=item['override'],**s))
with (out/'summary.csv').open('w',newline='',encoding='utf-8-sig') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
(out/'summary.json').write_text(json.dumps(rows,indent=2));(out/'README.txt').write_text('两机补跑一轮。相同90282e2 Host/Kernel支持8/16/32发送核；send-only，无接收扫描；每次5warmup10measured。original为原始随机路由五策略；intra/cross为等端点及逐边负载的七peer无SELF校准，每rank128Record。两种输入类型分开比较。override0沿用T01/02=8/16核；override32相邻编号重复，仅选奇数主统计；matched只选T01/T02不过滤任务。mode0混合、1组内、2跨组、3跨组优先、4组内优先。\n时间为每轮16rank本地发送排空时间max，非跨卡同步makespan；所有输入/输出/保护区检查通过。原始逐核profile在JSONL profiles字段；trace为最后轮单rank核寿命，不是native msprof。\n',encoding='utf-8')
files=[dict(path=p.relative_to(out).as_posix(),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in out.rglob('*') if p.is_file() and p.name!='files.sha256.json'];(out/'files.sha256.json').write_text(json.dumps(files,indent=2))
archive=out.with_suffix('.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for p in out.rglob('*'):
  if p.is_file():z.write(p,p.relative_to(out).as_posix())
print(json.dumps(dict(archive=str(archive),archive_bytes=archive.stat().st_size,archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),files=len(files)+1,summary=rows),ensure_ascii=False))
