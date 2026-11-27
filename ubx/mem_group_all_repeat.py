import concurrent.futures,json,shlex,statistics,subprocess,uuid
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
run='repeat-all-'+uuid.uuid4().hex[:10]
remote=r'''
import hashlib,json,shlex,statistics,subprocess
from pathlib import Path
from collections import defaultdict
base=Path('/root/ubx-mem-group-32core-d23ffa56');receipt=json.loads((base/'audit-receipt.json').read_text());assert receipt['status']=='PASS'
assert hashlib.sha256((base/'build/mem_group.o').read_bytes()).hexdigest()==receipt['kernel_sha256']
root=base/run;root.mkdir(exist_ok=False);source=base/'source/dispatch/mem-group-bench';runtime=Path('/root/ubx-dispatch-global-ab-d23ffa56/runtime-install');results=[]
patterns=[('original','/root/ubx-mem-group-8lane-d23ffa56/inputs/expert_ids.bin'),('intra','/root/ubx-mem-group-matched-d23ffa56/inputs/intra.bin'),('cross','/root/ubx-mem-group-matched-d23ffa56/inputs/cross.bin')]
for index,(pattern,inputfile) in enumerate(patterns):
 for override in [0,32]:
  name=pattern+'-'+str(override);output=root/name
  cmd=['python3',str(source/'run.py'),'--build',str(base/'build'),'--runtime',str(runtime),'--routes',inputfile,'--output',str(output),'--port',str(37000+index*100+override),'--repetitions','1','--warmup','5','--measured','10','--detail','0','--diagnostic','send-only','--send-cores',str(override)]
  p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=1400);(root/(name+'.stdout.log')).write_text(p.stdout);(root/(name+'.stderr.log')).write_text(p.stderr)
  if p.returncode:print(json.dumps(dict(host=host,status='FAIL',case=name,exit=p.returncode,tail=p.stderr.splitlines()[-5:])));raise SystemExit(1)
  assert json.loads((output/'result.json').read_text())['status']=='PASS';groups=defaultdict(list)
  for rank in range(16):
   rr=[json.loads(s) for s in (output/('rank%d.jsonl'%rank)).read_text().splitlines()];assert len(rr)==150 and all(r['verified'] for r in rr)
   for r in rr:
    # override32的相邻编号为重复用例，主统计只取奇数；匹配输入仅比较不过滤的混合任务。
    n=int(r['test'][1:])
    if override==32 and n%2==0:continue
    if pattern!='original' and n not in [1,2]:continue
    if not r['warmup']:groups[(r['test'],r['round'])].append(r)
  summary=[];tests=sorted({t for t,_ in groups})
  for test in tests:
   blocks=[v for (t,_),v in groups.items() if t==test];assert len(blocks)==10 and all(len(v)==16 for v in blocks);v=sorted(max(r['send_us'] for r in block) for block in blocks);sample=blocks[0][0]
   summary.append(dict(test=test,mode=sample['mode'],send_cores=sample['send_cores'],count=10,mean_us=statistics.mean(v),p95_us=v[8]+.55*(v[9]-v[8]),max_us=max(v),records_per_server=sum(r['records_sent'] for r in blocks[0])))
  results.append(dict(pattern=pattern,override=override,directory=str(output),status='PASS',summary=summary))
result=dict(host=host,root=str(root),revision=receipt['revision'],kernel_sha256=receipt['kernel_sha256'],status='PASS',results=results);(root/'repeat-receipt.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input='run='+repr(run)+'\nhost='+repr(host)+'\n'+remote,capture_output=True,text=True,timeout=8600)
 Path('repeat-all-'+host+'.stdout.log').write_text(p.stdout);Path('repeat-all-'+host+'.stderr.log').write_text(p.stderr);assert p.returncode==0,'Inspect saved repeat logs '+host
 return json.loads(p.stdout)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for r in pool.map(one,['90.90.93.81','90.90.93.83']):print(json.dumps(r),flush=True)
