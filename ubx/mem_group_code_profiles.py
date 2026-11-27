import hashlib,json,subprocess,tempfile,zipfile,io
from pathlib import Path
revision='62089e6ef70c808b13e35ca85aaaaaf5370d65cc'
checkout=Path(tempfile.mkdtemp(prefix='ubx-profile-source-'))/'ubx'
p=subprocess.run(['git','clone','--filter=blob:none','--no-checkout','--branch','codex/mem-group-8lane-bench','https://github.com/qizhiliu0639/UBX.git',str(checkout)],capture_output=True,timeout=180);assert p.returncode==0
p=subprocess.run(['git','-C',str(checkout),'archive','--format=zip',revision,'dispatch/mem-group-bench'],capture_output=True,timeout=90);assert p.returncode==0
out=Path('D:/work/UBX攻关/UBX_Control/mem-group-code-and-profiling');out.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(io.BytesIO(p.stdout)) as z:z.extractall(out/'source')
base=out.parent/'mem-group-8lane-observation-audit';count=0
for host in ['90.90.93.81','90.90.93.83']:
 for mode in ['normal','send-only','ready-probe']:
  for rank in range(16):
   src=base/host/'raw'/mode/('rank%d.jsonl'%rank);assert src.is_file()
   for r in map(json.loads,src.read_text().splitlines()):
    if r['round']!=14 or r['test'] not in ['T02','T04','T06','T08','T10']:continue
    start=min(c['start'] for c in r['profiles']);events=[]
    for c in r['profiles']:
     role='SEND' if c['core']<r['send_cores'] else 'OBSERVE'
     events.append(dict(name=role,cat='core lifetime',ph='X',pid=rank,tid=c['core'],ts=(c['start']-start)/1000,dur=(c['end']-c['start'])/1000,args=dict(records=c['records'],polls=c['polls'],wait_ticks=c['wait_ticks'])))
    dest=out/'traces'/host/mode/r['test']/('rank%d.trace.json'%rank);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(dict(traceEvents=events,displayTimeUnit='us',metadata=dict(host=host,diagnostic=mode,test=r['test'],rank=rank,round=14,clock='rank local; not aligned across devices'))));count+=1
(out/'README.txt').write_text('源码位于source/dispatch/mem-group-bench。源码commit62089e6；设备kernel与实测1a3787a相同，诊断Host实测949672d。\ntraces为原始逐核start/end转换的Chrome trace，可在Perfetto UI或chrome://tracing打开；每个文件只含单rank最后一轮的发送/观察核寿命，不是native msprof，不含逐次DMA时间线。每rank独立归零，不可拼接解释为跨卡同时开始。\n全部轮次原始profiling位于同级mem-group-8lane-observation-audit/<host>/raw/<mode>/rank*.jsonl的profiles数组。详细Lane上界数据见同级mem-group-8lane-bs32-cross-server/<host>/analysis/core_profiles.csv，其中detail=1才采集等待。\n',encoding='utf-8')
archive=out.with_suffix('.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for f in out.rglob('*'):
  if f.is_file():z.write(f,f.relative_to(out).as_posix())
print(json.dumps(dict(directory=str(out),archive=str(archive),trace_files=count,source_revision=revision,archive_bytes=archive.stat().st_size,archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest()),ensure_ascii=False))
