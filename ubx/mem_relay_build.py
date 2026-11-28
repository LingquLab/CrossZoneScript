import concurrent.futures,json,shlex,subprocess,tempfile
from pathlib import Path
REV='2a641ac4a647dc7232fcbfb70676082058143e70';ROOT='/root/ubx-mem-relay-d23ffa56'
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
checkout=Path(tempfile.mkdtemp(prefix='ubx-mem-relay-'))/'ubx'
p=subprocess.run(['git','clone','--filter=blob:none','--no-checkout','--branch','codex/mem-group-relay-bench','https://github.com/qizhiliu0639/UBX.git',str(checkout)],capture_output=True,timeout=180);assert p.returncode==0
archive=subprocess.run(['git','-C',str(checkout),'archive',REV,'dispatch/stage2/mem-relay-bench'],capture_output=True,timeout=90);assert archive.returncode==0
remote=r'''
import hashlib,json,shlex,subprocess
from pathlib import Path
root=Path(ROOT);source=root/'source/dispatch/stage2/mem-relay-bench';build=root/'build';runtime=Path('/root/ubx-dispatch-global-ab-d23ffa56/runtime-install')
stages=[]
for name,cmd in [('configure',['cmake','-S',str(source),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release','-DTILEXR_INSTALL='+str(runtime)]),('build',['cmake','--build',str(build),'-j4']),('cpu-test',['ctest','--test-dir',str(build),'--output-on-failure'])]:
 p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=420)
 text=p.stdout+p.stderr;(root/(name+'.log')).write_text(text);stages.append(dict(stage=name,exit=p.returncode,errors=[s for s in text.splitlines() if 'error:' in s or 'Error' in s][-6:]))
 if p.returncode:break
receipt=dict(host=host,root=str(root),revision=REV,status='PASS' if all(s['exit']==0 for s in stages) and len(stages)==3 else 'FAIL',stages=stages,npu_launched=False)
if receipt['status']=='PASS':receipt['kernel_sha256']=hashlib.sha256((build/'mem_group.o').read_bytes()).hexdigest()
(root/'build-receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
'''
def one(host):
 init='from pathlib import Path;p=Path('+repr(ROOT+'/source')+');assert not p.exists(),"Existing source; inspect before retry";p.mkdir(parents=True)'
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=init,text=True,capture_output=True,timeout=25);assert p.returncode==0
 p=subprocess.run(ssh+['root@'+host,'tar -xf - -C '+shlex.quote(ROOT+'/source')],input=archive.stdout,capture_output=True,timeout=120);assert p.returncode==0
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input='ROOT='+repr(ROOT)+'\nREV='+repr(REV)+'\nhost='+repr(host)+'\n'+remote,text=True,capture_output=True,timeout=1300)
 Path('relay-build-'+host+'.stdout.log').write_text(p.stdout);Path('relay-build-'+host+'.stderr.log').write_text(p.stderr);assert p.returncode==0
 r=json.loads(p.stdout);print(json.dumps(r),flush=True);return r
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,['90.90.93.81','90.90.93.83']))
assert all(r['status']=='PASS' for r in results),'Build failed; read saved stage evidence'
