import concurrent.futures,json,shlex,subprocess,tempfile
from pathlib import Path
REV='8c57052b811455340b85154f51a6beddec17d29a';ROOT='/root/ubx-mem-group-matched-d23ffa56'
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
repo=Path(tempfile.mkdtemp(prefix='ubx-matched-'))/'repo';p=subprocess.run(['git','clone','--filter=blob:none','--no-checkout','--branch','codex/mem-group-8lane-bench','https://github.com/qizhiliu0639/UBX.git',str(repo)],capture_output=True,timeout=180);assert p.returncode==0
archive=subprocess.run(['git','-C',str(repo),'archive',REV,'dispatch/mem-group-bench/run.py','dispatch/mem-group-bench/tests/matched_inputs.py'],capture_output=True,timeout=90);assert archive.returncode==0
remote=r'''
import hashlib,json,shlex,statistics,subprocess
from pathlib import Path
from collections import defaultdict
root=Path(ROOT);source=root/'source/dispatch/mem-group-bench';build=Path('/root/ubx-mem-group-audit-d23ffa56/build');runtime=Path('/root/ubx-dispatch-global-ab-d23ffa56/runtime-install')
assert hashlib.sha256((build/'mem_group.o').read_bytes()).hexdigest()=='fa7f52abc5021e8adb5756292d2d9aac98fc1587141f6b238b13e6fecea948af'
p=subprocess.run(['python3',str(source/'tests/matched_inputs.py'),str(root/'inputs')],capture_output=True,text=True,timeout=30);(root/'input-generation.log').write_text(p.stdout+p.stderr);assert p.returncode==0
results=[]
for index,(phase,pattern) in enumerate([('A1','intra'),('B1','cross'),('B2','cross'),('A2','intra')]):
 output=root/phase;cmd=['python3',str(source/'run.py'),'--build',str(build),'--runtime',str(runtime),'--routes',str(root/'inputs'/(pattern+'.bin')),'--output',str(output),'--port',str(35000+index*30),'--repetitions','1','--warmup','5','--measured','10','--detail','0','--diagnostic','send-only']
 p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=1400);(root/(phase+'.stdout.log')).write_text(p.stdout);(root/(phase+'.stderr.log')).write_text(p.stderr)
 if p.returncode:print(json.dumps(dict(host=host,status='FAIL',phase=phase,exit=p.returncode,tail=p.stderr.splitlines()[-4:])));raise SystemExit(1)
 assert json.loads((output/'result.json').read_text())['status']=='PASS';groups=defaultdict(list)
 for rank in range(16):
  rows=[json.loads(s) for s in (output/('rank%d.jsonl'%rank)).read_text().splitlines()];assert len(rows)==150
  for r in rows:
   assert r['verified'] and r['records_received']==0
   if r['test'] in ['T01','T02']:
    assert r['records_sent']==128 and r['category_records'][0]==0
    assert r['category_records']==([0,128,0] if pattern=='intra' else [0,0,128])
    if not r['warmup']:groups[(r['test'],r['round'])].append(r)
 summary=[]
 for test in ['T01','T02']:
  values=[max(r['send_us'] for r in v) for (t,_),v in groups.items() if t==test];assert len(values)==10
  v=sorted(values);summary.append(dict(test=test,send_cores=8 if test=='T01' else 16,count=10,mean_us=statistics.mean(v),p95_us=v[8]+(v[9]-v[8])*.55,max_us=max(v)))
 results.append(dict(phase=phase,pattern=pattern,status='PASS',summary=summary))
receipt=dict(host=host,root=str(root),source_revision=REV,status='PASS',kernel_sha256=hashlib.sha256((build/'mem_group.o').read_bytes()).hexdigest(),input_manifest=json.loads((root/'inputs/manifest.json').read_text()),results=results)
(root/'receipt.json').write_text(json.dumps(receipt,indent=2));receipt.pop('input_manifest');print(json.dumps(receipt))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input='from pathlib import Path;p=Path('+repr(ROOT+'/source')+');assert not p.exists();p.mkdir(parents=True)',capture_output=True,text=True,timeout=25);assert p.returncode==0
 p=subprocess.run(ssh+['root@'+host,'tar -xf - -C '+shlex.quote(ROOT+'/source')],input=archive.stdout,capture_output=True,timeout=90);assert p.returncode==0
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input='ROOT='+repr(ROOT)+'\nREV='+repr(REV)+'\nhost='+repr(host)+'\n'+remote,capture_output=True,text=True,timeout=5800)
 Path('matched-'+host+'.stdout.log').write_text(p.stdout);Path('matched-'+host+'.stderr.log').write_text(p.stderr);assert p.returncode==0,'Inspect saved matched logs '+host
 return json.loads(p.stdout)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for r in pool.map(one,['90.90.93.81','90.90.93.83']):print(json.dumps(r),flush=True)
