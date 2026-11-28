# 中文：确认生产源码未变后复用已验证产物，只重编译修正布局的夹具并部署。
# English: Verify unchanged production sources, reuse proven artifacts, rebuild the corrected fixture and deploy.
import hashlib,json,shlex,subprocess,tempfile
from pathlib import Path
import sys
settings=json.loads((Path(__file__).resolve().parents[2]/'args.json').read_text())
REV=settings['revision'];assert len(REV)==40 and all(c in '0123456789abcdef' for c in REV)
ROOT='/root/ubx-g8-control-b212b5f4-r3'
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
repo=Path('D:/code-project/TileXR-shared-off25-cad5b90');p=subprocess.run(['git','-C',str(repo),'fetch','origin','codex/ubx-g8-control-overlap'],capture_output=True,timeout=180);assert p.returncode==0
assert subprocess.run(['git','-C',str(repo),'diff','--quiet','e7d5d047fc3f3e41f3356dc5d190dc54b88b4913',REV,'--','src/','CMakeLists.txt'],capture_output=True,timeout=30).returncode==0,'Production sources changed; full build required'
p=subprocess.run(ssh+['root@90.90.93.81','python3 -'],input='from pathlib import Path;p=Path('+repr(ROOT+'/source')+');assert not p.exists();p.mkdir(parents=True)',capture_output=True,text=True,timeout=25);assert p.returncode==0
with tempfile.TemporaryFile() as archive:
 p=subprocess.run(['git','-C',str(repo),'archive',REV],stdout=archive,stderr=subprocess.PIPE,timeout=90);assert p.returncode==0
 archive.seek(0);p=subprocess.run(ssh+['root@90.90.93.81','tar -xf - -C '+shlex.quote(ROOT+'/source')],stdin=archive,capture_output=True,timeout=180);assert p.returncode==0
remote=r'''
import hashlib,json,random,re,shlex,shutil,struct,subprocess
from pathlib import Path
root=Path(ROOT);(root/'inputs').mkdir()
for n in ['spdlog','picojson']:shutil.copytree(Path('/root/ubx-relay-bs32-84da56a-exclusive/runtime/3rdparty')/n,root/'source/3rdparty'/n,dirs_exist_ok=True)
routes={}
for bs in [32]:
 rng=random.Random(20261127);v=[x for _ in range(32*bs) for x in rng.sample(range(384),8)];data=struct.pack('<%di'%len(v),*v);(root/'inputs'/('routes-bs%d.bin'%bs)).write_bytes(data);routes[str(bs)]=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
base=Path('/root/ubx-g8-control-b212b5f4-r2')
old=json.loads((base/'build-receipt.json').read_text());assert old['revision']=='e7d5d047fc3f3e41f3356dc5d190dc54b88b4913'
assert all(hashlib.sha256((base/n).read_bytes()).hexdigest()==h for n,h in old['artifacts'].items())
shutil.copytree(base/'group8',root/'group8')
steps=[]
for variant in ['group8']:
 flags=['-DCMAKE_INSTALL_PREFIX='+str(root/variant),'-DTILEXR_BUILD_EP=ON','-DTILEXR_BUILD_RDMA=ON','-DTILEXR_BUILD_DIAGNOSTICS=ON','-DTILEXR_BUILD_TESTS=OFF','-DTILEXR_BUILD_UNITEST=OFF','-DTILEXR_BUILD_COLLECTIVES=OFF','-DTILEXR_BUILD_MOONEP=OFF','-DTILEXR_BUILD_MOONEP_PLANNER=OFF','-DTILEXR_BUILD_RELAY=OFF','-DTILEXR_BUILD_CHECKER=OFF','-DTILEXR_EP_ENABLE_PROFILING=ON','-DTILEXR_EP_UBX_RDMA_GROUP8='+('ON' if variant=='group8' else 'OFF'),'-DTILEXR_DIAGNOSTICS_SOC_TYPE=Ascend950','-DTILEXR_EP_UBX_FANOUT_COPIES=OFF','-DTILEXR_EP_UBX_PACKET_TRACE=OFF']
 cmds=[['cmake','-S',str(root/'source/tests/ep/ubx_full_intra'),'-B',str(root/'fixture-group8-build'),'-DTILEXR_HOST_PREFIX='+str(root/'group8'),'-DCMAKE_INSTALL_PREFIX='+str(root/'group8'),'-DASCEND_HOME_PATH=/usr/local/Ascend/cann','-DTILEXR_FIXTURE_BUILD_ISOLATED=OFF','-DTILEXR_FIXTURE_BARRIER_PROFILING=OFF'],['cmake','--build',str(root/'fixture-group8-build'),'-j8'],['cmake','--install',str(root/'fixture-group8-build')]]
 for i,cmd in enumerate(cmds):
  p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=600);text=p.stdout+p.stderr;(root/('%s-stage%d.log'%(variant,i))).write_text(text);steps.append(dict(variant=variant,stage=i,exit=p.returncode,errors=[s[:1800] for s in text.splitlines() if re.search(r'\berror:|fatal error|undefined reference',s,re.I)][:5]))
  if p.returncode:print(json.dumps(dict(status='FAIL',steps=steps)));raise SystemExit(1)
artifacts={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for d in ['group8','inputs'] for p in (root/d).rglob('*') if p.is_file()}
receipt=dict(revision=REV,production_binary_revision=old['revision'],status='PASS',steps=steps,routes=routes,artifacts=artifacts,ep=32,experts=384,hidden=3584,topk=8,warmup=5,measured=10,max_tokens=[2],profiling=['on'],variants=['group8'],native_msprof=False)
(root/'build-receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(dict(status='PASS',revision=REV,steps=steps,routes=routes,artifact_count=len(artifacts),npu_launched=False)))
'''
p=subprocess.run(ssh+['root@90.90.93.81','python3 -'],input='ROOT='+repr(ROOT)+'\nREV='+repr(REV)+'\n'+remote,capture_output=True,text=True,timeout=7400);Path('group8-overlap-build.stdout.log').write_text(p.stdout);Path('group8-overlap-build.stderr.log').write_text(p.stderr);print(p.stdout,flush=True);assert p.returncode==0,'Inspect saved build evidence'
for deploymentHost in ['90.90.93.83','90.90.93.86']:
 p=subprocess.run(ssh+['root@'+deploymentHost,'python3 -'],input='from pathlib import Path;p=Path('+repr(ROOT)+');assert not p.exists();p.mkdir()',capture_output=True,text=True,timeout=25);assert p.returncode==0
 with tempfile.TemporaryFile() as archive:
  p=subprocess.run(ssh+['root@90.90.93.81','tar -czf - -C '+shlex.quote(ROOT)+' group8 inputs build-receipt.json'],stdout=archive,stderr=subprocess.PIPE,timeout=180);assert p.returncode==0
  archive.seek(0);p=subprocess.run(ssh+['root@'+deploymentHost,'tar -xzf - -C '+shlex.quote(ROOT)],stdin=archive,capture_output=True,timeout=180);assert p.returncode==0
 verify='import json,hashlib;from pathlib import Path;r=Path('+repr(ROOT)+');d=json.loads((r/"build-receipt.json").read_text());assert all(hashlib.sha256((r/n).read_bytes()).hexdigest()==h for n,h in d["artifacts"].items());print(json.dumps(dict(deployed=True,host='+repr(deploymentHost)+',artifact_count=len(d["artifacts"]),npu_launched=False)))'
 p=subprocess.run(ssh+['root@'+deploymentHost,'python3 -'],input=verify,text=True,capture_output=True,timeout=90);assert p.returncode==0;print(p.stdout)
