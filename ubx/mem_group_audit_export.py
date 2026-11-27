import base64,concurrent.futures,csv,hashlib,io,json,subprocess,zipfile
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
remote=r'''
import base64,hashlib,io,json,zipfile,csv,statistics
from pathlib import Path
from collections import defaultdict
root=Path('/root/ubx-mem-group-audit-d23ffa56');receipt=json.loads((root/'audit-receipt.json').read_text());assert receipt['status']=='PASS'
allrows=[];rounds=[]
for mode in ['normal','ready-probe','send-only']:
 groups=defaultdict(list)
 for rank in range(16):
  rr=[json.loads(s) for s in (root/mode/('rank%d.jsonl'%rank)).read_text().splitlines()];assert len(rr)==150
  for r in rr:
   assert r['verified'];row={k:v for k,v in r.items() if k not in ['profiles','category_records']};row.update(host=host,planned_self_records=r['category_records'][0],planned_same_group_peer_records=r['category_records'][1],planned_cross_group_records=r['category_records'][2]);allrows.append(row)
   if not r['warmup']:groups[(r['test'],r['round'])].append(r)
 for (test,roundno),rr in sorted(groups.items()):
  assert len(rr)==16;rounds.append(dict(host=host,diagnostic=mode,test=test,round=roundno,slowest_send_us=max(r['send_us'] for r in rr),slowest_receive_us=max(r['receive_us'] for r in rr),records_sent=sum(r['records_sent'] for r in rr),records_observed=sum(r['records_received'] for r in rr)))
report=root/'export';report.mkdir(exist_ok=True)
for name,rows in [('all_rank_rounds.csv',allrows),('per_round.csv',rounds)]:
 with (report/name).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
payload=io.BytesIO()
with zipfile.ZipFile(payload,'w',zipfile.ZIP_DEFLATED) as z:
 z.write(root/'audit-receipt.json','audit-receipt.json')
 for f in report.iterdir():z.write(f,'analysis/'+f.name)
 for mode in ['normal','ready-probe','send-only']:
  for name in ['config.json','result.json','expert_ids.bin','groups.bin']:z.write(root/mode/name,'raw/'+mode+'/'+name)
  for rank in range(16):z.write(root/mode/('rank%d.jsonl'%rank),'raw/'+mode+'/rank%d.jsonl'%rank)
assert len(payload.getvalue())<32*1024*1024
print(json.dumps(dict(archive_b64=base64.b64encode(payload.getvalue()).decode(),sha256=hashlib.sha256(payload.getvalue()).hexdigest(),summary=receipt['results'])))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input='host='+repr(host)+'\n'+remote,text=True,capture_output=True,timeout=180)
 Path('audit-export-'+host+'.stderr.log').write_text(p.stderr);assert p.returncode==0;return host,json.loads(p.stdout)
out=Path('D:/work/UBX攻关/UBX_Control/mem-group-8lane-observation-audit');out.mkdir(parents=True,exist_ok=True);summaries=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,['90.90.93.81','90.90.93.83']))
for host,result in results:
 blob=base64.b64decode(result['archive_b64']);assert hashlib.sha256(blob).hexdigest()==result['sha256']
 with zipfile.ZipFile(io.BytesIO(blob)) as z:
  for member in z.infolist():
   name=Path(member.filename);assert not name.is_absolute() and '..' not in name.parts
   dest=out/host/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(z.read(member))
 for mode in result['summary']:
  for r in mode['summary']:summaries.append(dict(host=host,diagnostic=mode['mode'],**r))
with (out/'summary.csv').open('w',newline='',encoding='utf-8-sig') as f:w=csv.DictWriter(f,fieldnames=list(summaries[0]));w.writeheader();w.writerows(summaries)
(out/'summary.json').write_text(json.dumps(summaries,indent=2))
(out/'README.txt').write_text('接收观测开销诊断；两机各自16rank，每个用例5轮预热+10轮测量，1次重复。设备ELF与原实验逐字节相同。\nnormal：正常发送并发接收扫描。send-only：发送任务不变，观察范围设为空，计时后Host全体汇合再完整校验入站数据；其receive_us仅为空观察核退出时间，不是接收完成时间。ready-probe：计时前上传完整预期接收Record，发送任务设为空，原扫描算法不变；其send_us仅为空发送核退出时间，不是通信时间。\nready-probe读取缓存状态与normal不同，不能用它的时间直接扣减normal接收时间。category_records是原计划任务分类，ready-probe实际发送为0；CSV已明确命名planned_*，不得用这些数量计算ready-probe通信带宽。\n本对照只用于判断扫描开销和观察流量对发送的干扰；原始16rank输出按服务器分别保存，包括输入和配置。\n',encoding='utf-8')
entries=[dict(path=p.relative_to(out).as_posix(),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='files.sha256.json'];(out/'files.sha256.json').write_text(json.dumps(entries,indent=2))
archive=out.with_suffix('.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for p in sorted(out.rglob('*')):
  if p.is_file():z.write(p,p.relative_to(out).as_posix())
print(json.dumps(dict(archive=str(archive),archive_bytes=archive.stat().st_size,archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),files=len(entries)+1),ensure_ascii=False))
