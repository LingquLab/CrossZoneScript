import base64,concurrent.futures,csv,hashlib,io,json,subprocess,uuid,zipfile
from pathlib import Path
settings=json.loads((Path(__file__).resolve().parents[2]/'args.json').read_text(encoding='utf-8-sig'))
formal=settings['formal'];detail=settings['detail'];assert formal.startswith('formal-') and detail.startswith('detail-')
helper=Path(__file__).with_name('mem_group_summarize.py').read_text()
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
remote=r'''
import base64,hashlib,io,json,subprocess,zipfile
from pathlib import Path
root=Path('/root/ubx-mem-group-8lane-d23ffa56');report=root/('analysis-'+formal)
for run in [formal,detail]:
 result=json.loads((root/run/'result.json').read_text());assert result['status']=='PASS' and result['ranks']==16
 config=json.loads((root/run/'config.json').read_text());assert config['warmup']==5 and config['measured']==10
script=root/'summarize-export.py';script.write_text(helper)
p=subprocess.run(['python3',str(script),'--input',host+'='+str(root/formal),'--input',host+'='+str(root/detail),'--output',str(report)],capture_output=True,text=True,timeout=180)
(root/'summarize-export.stdout.log').write_text(p.stdout);(root/'summarize-export.stderr.log').write_text(p.stderr);assert p.returncode==0
summary=json.loads((report/'summary.json').read_text());assert len(summary)==20
payload=io.BytesIO()
with zipfile.ZipFile(payload,'w',zipfile.ZIP_DEFLATED) as z:
 for f in report.iterdir():
  if f.is_file():z.write(f,'analysis/'+f.name)
 for name in ['topology.txt','topology-help.txt','build-receipt.json']:z.write(root/name,'configuration/'+name)
 z.write(root/'inputs/expert_ids.bin','inputs/expert_ids.bin')
 for run in [formal,detail]:
  for name in ['config.json','result.json','groups.bin']:z.write(root/run/name,'raw/'+run+'/'+name)
  for rank in range(16):z.write(root/run/('rank%d.jsonl'%rank),'raw/'+run+'/rank%d.jsonl'%rank)
assert len(payload.getvalue())<32*1024*1024
print(json.dumps(dict(archive_b64=base64.b64encode(payload.getvalue()).decode(),archive_sha256=hashlib.sha256(payload.getvalue()).hexdigest(),summary=summary)))
'''
def one(host):
 script='formal='+repr(formal)+'\ndetail='+repr(detail)+'\nhost='+repr(host)+'\nhelper='+repr(helper)+'\n'+remote
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=script,text=True,capture_output=True,timeout=240)
 Path('export-'+host+'.stderr.log').write_text(p.stderr);assert p.returncode==0,'export '+host
 result=json.loads(p.stdout);return host,result
out=Path('D:/work/UBX攻关/UBX_Control/mem-group-8lane-bs32-cross-server');out.mkdir(parents=True,exist_ok=True);summaries=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 results=list(pool.map(one,['90.90.93.81','90.90.93.83']))
for host,result in results:
 raw=base64.b64decode(result['archive_b64']);assert hashlib.sha256(raw).hexdigest()==result['archive_sha256'];dest=out/host;dest.mkdir(exist_ok=True)
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  for f in z.infolist():
   name=Path(f.filename);assert not name.is_absolute() and '..' not in name.parts
   target=dest/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(f))
 summaries.extend(result['summary'])
with (out/'comparison_summary.csv').open('w',newline='',encoding='utf-8-sig') as f:
 w=csv.DictWriter(f,fieldnames=list(summaries[0]));w.writeheader();w.writerows(summaries)
(out/'comparison_summary.json').write_text(json.dumps(summaries,indent=2))
(out/'README.txt').write_text('两机各自单机16卡T01～T10；8 Lane；固定8观察核；正式轻量采样每个用例三次重复，每次5轮预热+10轮测量；detail独立采样同样5+10但仅一次重复。detail=0为主要性能数据，detail=1含额外同步，仅用于Lane复用等待上界诊断。\n按每轮16rank本地receive_us取最大值，统计均值/P95/max，不对跨卡绝对时间戳相减。该值包含轮询观察延迟及启动偏斜，不是严格标定的server物理makespan。\n默认8+8为逻辑分组；两机拓扑相同，但矩阵还显示4卡直连关系，不能仅凭此确认两个独立8卡通信域。SELF单列并从跨卡带宽分子剔除。GB/s为字节/微秒/1000。record4096B，hidden payload3584B。全部输入预就绪且预打包，不计RDMA、路由计算和最终copyout。\n每台服务器目录包含原始rank JSONL、逐轮和逐核CSV、输入expert_ids.bin、groups.bin、运行配置、构建哈希和拓扑。kernel/Host实测版本1a3787a6fe5872e86bb8ba79ec58d3c87215a7c1，后续仓库默认轮数变更由显式参数覆盖。\n',encoding='utf-8')
files=[dict(path=p.relative_to(out).as_posix(),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='files.sha256.json'];(out/'files.sha256.json').write_text(json.dumps(files,indent=2))
archive=out.with_suffix('.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for p in sorted(out.rglob('*')):
  if p.is_file():z.write(p,p.relative_to(out).as_posix())
compact=[]
for r in summaries:
 compact.append({k:r[k] for k in ['host','detail','test','send_cores','slowest_rank_receive_us_mean','slowest_rank_receive_us_p95','slowest_rank_receive_us_maximum','slowest_rank_send_us_mean','send_aiv_mean_us_mean','send_aiv_max_us_mean','observed_wire_GBps_mean','lane_wait_upper_bound_sum_us_mean','self_records','same_group_peer_records','cross_group_records','cross_card_wire_bytes']})
print(json.dumps(dict(directory=str(out),archive=str(archive),archive_bytes=archive.stat().st_size,archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),files=len(files)+1,formal=formal,detail=detail,summary=compact),ensure_ascii=False))
