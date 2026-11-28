import json,shlex,subprocess,tempfile
from pathlib import Path
REV='c6cabdac1888c713f3196b889155839c50a1ab6f';ROOT='/root/ubx-mem-relay-shared-d23ffa56'
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
checkout=Path(tempfile.mkdtemp(prefix='relay-shared-'))/'ubx'
p=subprocess.run(['git','clone','--filter=blob:none','--no-checkout','--branch','codex/mem-group-relay-bench','https://github.com/qizhiliu0639/UBX.git',str(checkout)],capture_output=True,timeout=180);assert p.returncode==0
archive=subprocess.run(['git','-C',str(checkout),'archive',REV,'dispatch/stage2/mem-relay-bench'],capture_output=True,timeout=90);assert archive.returncode==0
init='from pathlib import Path;p=Path('+repr(ROOT+'/source')+');assert not p.exists(),"Inspect existing task before retry";p.mkdir(parents=True)'
p=subprocess.run(ssh+['root@90.90.93.86','python3 -'],input=init,text=True,capture_output=True,timeout=30);assert p.returncode==0
p=subprocess.run(ssh+['root@90.90.93.86','tar -xf - -C '+shlex.quote(ROOT+'/source')],input=archive.stdout,capture_output=True,timeout=120);assert p.returncode==0
remote=r'''
import hashlib,json,shlex,subprocess
from pathlib import Path
root=Path(ROOT);source=root/'source/dispatch/stage2/mem-relay-bench';build=root/'build';runtime=Path('/root/ubx-dispatch-global-ab-d23ffa56/runtime-install');routes=Path('/root/ubx-token-merge-matrix-d23ffa56/inputs/routes-bs32.bin')
assert hashlib.sha256(routes.read_bytes()).hexdigest()=='2a58c479564c82f5185d42bfa3608a5e9f2128e0125c921a1d6ab4ec18f63b24'
results=[]
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release','-DTILEXR_INSTALL='+str(runtime)]),('build',['cmake','--build',str(build),'-j4']),('cpu-test',['ctest','--test-dir',str(build),'--output-on-failure'])]
for phase,warm,measured,port in [('smoke',1,1,35400),('formal',5,10,35500)]:
 commands.append((phase,['python3',str(source/'run.py'),'--build',str(build),'--runtime',str(runtime),'--routes',str(routes),'--output',str(root/phase),'--port',str(port),'--warmup',str(warm),'--measured',str(measured)]))
for name,cmd in commands:
 p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=1300 if name in ['smoke','formal'] else 420)
 (root/(name+'.stdout.log')).write_text(p.stdout);(root/(name+'.stderr.log')).write_text(p.stderr)
 row=dict(stage=name,exit=p.returncode)
 if name in ['smoke','formal'] and p.returncode==0:row['result']=json.loads((root/name/'result.json').read_text())
 if p.returncode:
  row['tail']=(p.stdout+p.stderr).splitlines()[-8:]
  row['workers']=[dict(rank=i,tail=(root/name/('rank%d.log'%i)).read_text(errors='replace').splitlines()[-5:]) for i in [0,1,8] if (root/name/('rank%d.log'%i)).is_file()]
 results.append(row)
 if p.returncode:break
receipt=dict(host='90.90.93.86',revision=REV,root=str(root),completed=len(results)==5 and all(r['exit']==0 for r in results),results=results)
if (build/'mem_group.o').exists():receipt['kernel_sha256']=hashlib.sha256((build/'mem_group.o').read_bytes()).hexdigest()
(root/'receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
'''
p=subprocess.run(ssh+['root@90.90.93.86','python3 -'],input='ROOT='+repr(ROOT)+'\nREV='+repr(REV)+'\n'+remote,text=True,capture_output=True,timeout=3500)
Path('shared86.stdout.log').write_text(p.stdout);Path('shared86.stderr.log').write_text(p.stderr);assert p.returncode==0
r=json.loads(p.stdout);print(json.dumps(r));assert r['completed'],'Inspect saved results before retry'
