import concurrent.futures,json,shlex,subprocess,tempfile
from pathlib import Path
REV='949672d23e1b0def18773727ef5ce9c271c08ec6';ROOT='/root/ubx-mem-group-audit-d23ffa56'
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
checkout=Path(tempfile.mkdtemp(prefix='ubx-mem-observation-'))/'ubx';p=subprocess.run(['git','clone','--filter=blob:none','--no-checkout','--branch','codex/mem-group-8lane-bench','https://github.com/qizhiliu0639/UBX.git',str(checkout)],capture_output=True,timeout=180);assert p.returncode==0
archive=subprocess.run(['git','-C',str(checkout),'archive',REV,'dispatch/mem-group-bench'],capture_output=True,timeout=90);assert archive.returncode==0
remote=r'''
import hashlib,json,shlex,shutil,statistics,subprocess
from pathlib import Path
from collections import defaultdict
root=Path(ROOT);source=root/'source/dispatch/mem-group-bench';build=root/'build';old=Path('/root/ubx-mem-group-8lane-d23ffa56');runtime=Path('/root/ubx-dispatch-global-ab-d23ffa56/runtime-install')
stages=[]
for name,cmd in [('configure',['cmake','-S',str(source),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release','-DTILEXR_INSTALL='+str(runtime)]),('build',['cmake','--build',str(build),'-j4']),('cpu-test',['ctest','--test-dir',str(build),'--output-on-failure'])]:
 p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=420);text=p.stdout+p.stderr;(root/(name+'.log')).write_text(text);stages.append(dict(stage=name,exit=p.returncode,errors=[s for s in text.splitlines() if 'error:' in s or 'Error' in s][-6:]))
 if p.returncode:print(json.dumps(dict(host=host,revision=REV,status='BUILD_FAIL',stages=stages)));raise SystemExit(1)
# 精确复用已验证ELF，诊断只改变Host任务范围，不改变设备实现。
assert hashlib.sha256((old/'build/mem_group.o').read_bytes()).hexdigest()=='fa7f52abc5021e8adb5756292d2d9aac98fc1587141f6b238b13e6fecea948af'
assert (old/'source/dispatch/mem-group-bench/kernel/bench.cpp').read_bytes()==(source/'kernel/bench.cpp').read_bytes()
shutil.copy2(old/'build/mem_group.o',build/'mem_group.o')
results=[]
for index,mode in enumerate(['normal','ready-probe','send-only']):
 output=root/mode
 cmd=['python3',str(source/'run.py'),'--build',str(build),'--runtime',str(runtime),'--routes',str(old/'inputs/expert_ids.bin'),'--output',str(output),'--port',str(33000+index*30),'--repetitions','1','--warmup','5','--measured','10','--detail','0','--diagnostic',mode]
 p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=1400);(root/(mode+'.stdout.log')).write_text(p.stdout);(root/(mode+'.stderr.log')).write_text(p.stderr)
 if p.returncode:
  tails=[dict(rank=n,tail=(output/('rank%d.log'%n)).read_text(errors='replace').splitlines()[-3:]) for n in range(16) if (output/('rank%d.log'%n)).is_file()][:3]
  print(json.dumps(dict(host=host,status='RUN_FAIL',mode=mode,exit=p.returncode,runner_tail=p.stderr.splitlines()[-5:],worker_tail=tails,completed=results)));raise SystemExit(1)
 result=json.loads((output/'result.json').read_text());assert result['status']=='PASS';group=defaultdict(list)
 for rank in range(16):
  rr=[json.loads(s) for s in (output/('rank%d.jsonl'%rank)).read_text().splitlines()];assert len(rr)==150
  for r in rr:
   assert r['verified'] and r['diagnostic']==mode
   if mode=='send-only':assert r['records_received']==0 and sum(x['polls'] for x in r['profiles'][r['send_cores']:])==0
   if mode=='ready-probe':assert r['records_sent']==0 and sum(x['polls'] for x in r['profiles'][r['send_cores']:])==r['records_received']
   if not r['warmup']:group[(r['test'],r['round'])].append(r)
 summary=[]
 for test in ['T%02d'%n for n in range(1,11)]:
  rounds=[v for (t,_),v in group.items() if t==test];assert len(rounds)==10 and all(len(v)==16 for v in rounds)
  def metric(name):return statistics.mean(max(r[name] for r in v) for v in rounds)
  summary.append(dict(test=test,mean_slowest_send_us=metric('send_us'),mean_slowest_receive_us=metric('receive_us'),measured_rounds=10,source_records_per_round=sum(r['records_sent'] for r in rounds[0]),observed_records_per_round=sum(r['records_received'] for r in rounds[0])))
 results.append(dict(mode=mode,status='PASS',round_summaries=result['round_summaries'],summary=summary))
receipt=dict(host=host,root=str(root),revision=REV,kernel_sha256=hashlib.sha256((build/'mem_group.o').read_bytes()).hexdigest(),status='PASS',stages=stages,results=results)
(root/'audit-receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
'''
def one(host):
 init='from pathlib import Path;p=Path('+repr(ROOT+'/source')+');assert not p.exists(),"Existing source; inspect task before retry";p.mkdir(parents=True)'
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=init,text=True,capture_output=True,timeout=25);assert p.returncode==0
 p=subprocess.run(ssh+['root@'+host,'tar -xf - -C '+shlex.quote(ROOT+'/source')],input=archive.stdout,capture_output=True,timeout=120);assert p.returncode==0
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input='ROOT='+repr(ROOT)+'\nREV='+repr(REV)+'\nhost='+repr(host)+'\n'+remote,text=True,capture_output=True,timeout=5100)
 Path('controls-'+host+'.stdout.log').write_text(p.stdout);Path('controls-'+host+'.stderr.log').write_text(p.stderr)
 assert p.returncode==0,'Control test failed '+host+'; read saved stdout/stderr'
 r=json.loads(p.stdout);assert r['status']=='PASS';return r
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for result in pool.map(one,['90.90.93.81','90.90.93.83']):print(json.dumps(result),flush=True)
