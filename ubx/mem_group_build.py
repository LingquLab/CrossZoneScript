import concurrent.futures,hashlib,json,shlex,subprocess,tempfile
from pathlib import Path
REV='1a3787a6fe5872e86bb8ba79ec58d3c87215a7c1'
ROOT='/root/ubx-mem-group-8lane-d23ffa56'
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
checkout=Path(tempfile.mkdtemp(prefix='ubx-mem-group-source-'))/'ubx'
p=subprocess.run(['git','clone','--filter=blob:none','--no-checkout','--branch','codex/mem-group-8lane-bench','https://github.com/qizhiliu0639/UBX.git',str(checkout)],capture_output=True,timeout=180);assert p.returncode==0
archive=subprocess.run(['git','-C',str(checkout),'archive',REV,'dispatch/mem-group-bench'],capture_output=True,timeout=90);assert archive.returncode==0
remote=r'''
import concurrent.futures,hashlib,json,shlex,subprocess,shutil
from pathlib import Path
root=Path(ROOT);source=root/'source/dispatch/mem-group-bench';build=root/'build';runtime=Path('/root/ubx-dispatch-global-ab-d23ffa56/runtime-install')
assert (runtime/'lib/libtile-comm.so').is_file()
(root/'inputs').mkdir(exist_ok=True)
old=Path('/root/ubx-dispatch-global-ab-d23ffa56/inputs/ep32-full/bs32/expert_ids.bin');data=old.read_bytes();assert hashlib.sha256(data).hexdigest()=='2a58c479564c82f5185d42bfa3608a5e9f2128e0125c921a1d6ab4ec18f63b24';(root/'inputs/expert_ids.bin').write_bytes(data)
checks=[]
for name,cmd in [('topology',['npu-smi','info','-t','topo']),('topology-help',['npu-smi','info','-h'])]:
 p=subprocess.run(cmd,capture_output=True,text=True,timeout=20);(root/(name+'.txt')).write_text(p.stdout+p.stderr);checks.append(dict(check=name,exit=p.returncode,excerpt=(p.stdout+p.stderr).splitlines()[:22]))
def probe(d):
 p=subprocess.run(['npu-smi','info','-t','proc-mem','-i',str(d)],capture_output=True,text=True,timeout=15);return dict(device=d,idle=p.returncode==0 and 'No process in device.' in p.stdout and 'Process id' not in p.stdout)
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:cards=list(pool.map(probe,range(16)))
stages=[]
for name,cmd in [('configure',['cmake','-S',str(source),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release','-DTILEXR_INSTALL='+str(runtime)]),('build',['cmake','--build',str(build),'-j4']),('cpu-test',['ctest','--test-dir',str(build),'--output-on-failure'])]:
 p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=420);text=p.stdout+p.stderr;(root/(name+'.log')).write_text(text);stages.append(dict(stage=name,exit=p.returncode,errors=[s for s in text.splitlines() if 'error:' in s or 'Error' in s or 'tests passed' in s][-10:]))
 if p.returncode:break
receipt=dict(revision=REV,root=str(root),stages=stages,cards=cards,topology=checks,runtime_sha256=hashlib.sha256((runtime/'lib/libtile-comm.so').read_bytes()).hexdigest(),npu_launched=False)
if all(s['exit']==0 for s in stages):receipt['artifacts']={n:hashlib.sha256((build/n).read_bytes()).hexdigest() for n in ['mem_group.o','mem_group_worker']}
(root/'build-receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
'''
def one(host):
 init='from pathlib import Path;p=Path('+repr(ROOT+'/source')+');assert not p.exists(),"Existing source; inspect before retry";p.mkdir(parents=True)'
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=init,text=True,capture_output=True,timeout=25);assert p.returncode==0,'staging '+host
 p=subprocess.run(ssh+['root@'+host,'tar -xf - -C '+shlex.quote(ROOT+'/source')],input=archive.stdout,capture_output=True,timeout=120);assert p.returncode==0
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input='ROOT='+repr(ROOT)+'\nREV='+repr(REV)+'\n'+remote,text=True,capture_output=True,timeout=1000)
 Path('build-'+host+'.stderr.log').write_text(p.stderr);Path('build-'+host+'.stdout.log').write_text(p.stdout)
 assert p.returncode==0,'remote build '+host
 r=json.loads(p.stdout);r['host']=host;return r
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,['90.90.93.81','90.90.93.83']))
for r in results:print(json.dumps(r),flush=True)
assert all(all(s['exit']==0 for s in r['stages']) for r in results),'Build failed; read saved stage evidence'
