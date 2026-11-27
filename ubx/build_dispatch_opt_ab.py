# Build the requested UBX A/B variants and pinned runtime on the test server.
import json,subprocess,tempfile,shlex
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and p.stat().st_size<65536 and b'PRIVATE KEY' in p.read_bytes()]
assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
checkout=Path(tempfile.mkdtemp(prefix='ubx-dispatch-opt-source-'))
p=subprocess.run(['git','clone','--filter=blob:none','--no-checkout','https://github.com/qizhiliu0639/UBX.git',str(checkout/'ubx')],capture_output=True,timeout=180);assert p.returncode==0,'UBX source fetch failed'
p=subprocess.run(['git','-C',str(checkout/'ubx'),'fetch','origin','codex/dispatch-global-routes-ab'],capture_output=True,timeout=120);assert p.returncode==0
runtime=Path('D:/code-project/TileXR-shared-off25-cad5b90')
p=subprocess.run(['git','-C',str(runtime),'fetch','origin','0797410beab1da40800611affc9c512cd42525ba'],capture_output=True,timeout=120);assert p.returncode==0,'Pinned runtime fetch failed'
root='/root/ubx-dispatch-global-ab-d23ffa56'
def stage(repo,revision,target,paths=()):
 code='from pathlib import Path; p=Path('+repr(target)+'); assert not p.exists(),"Source already exists; inspect task before retry"; p.mkdir(parents=True)'
 p=subprocess.run(ssh+['root@90.90.93.81','python3 -'],input=code,text=True,capture_output=True,timeout=20);assert p.returncode==0,'Source staging failed'
 with tempfile.TemporaryFile() as archive:
  p=subprocess.run(['git','-C',str(repo),'archive',revision,*paths],stdout=archive,stderr=subprocess.PIPE,timeout=120);assert p.returncode==0
  archive.seek(0);p=subprocess.run(ssh+['root@90.90.93.81','tar -xf - -C '+shlex.quote(target)],stdin=archive,capture_output=True,timeout=180);assert p.returncode==0
stage(runtime,'0797410beab1da40800611affc9c512cd42525ba',root+'/runtime-source')
for variant,rev in [('control','771715cc8e2dd617a092246d8786f0576bb7fa81'),('global','501997c9b29ae3b9e9e33cb244d93e948718a96f')]:stage(checkout/'ubx',rev,root+'/'+variant+'/source',('dispatch/dispatch-release',))
remote=r'''import json,subprocess,shlex,shutil,hashlib
from pathlib import Path
root=Path('/root/ubx-dispatch-global-ab-d23ffa56')
for name in ['spdlog','picojson']:shutil.copytree(Path('/root/ubx-relay-bs32-84da56a-exclusive/runtime/3rdparty')/name,root/'runtime-source/3rdparty'/name,dirs_exist_ok=True)
def run(label,cmd,timeout=600):
 p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=timeout)
 (root/(label+'.log')).write_text(p.stdout+p.stderr)
 print(json.dumps({'stage':label,'exit':p.returncode,'tail':(p.stdout+p.stderr).splitlines()[-4:]}),flush=True)
 if p.returncode:raise SystemExit(1)
flags=['-DCMAKE_BUILD_TYPE=Release','-DCMAKE_INSTALL_PREFIX='+str(root/'runtime-install'),'-DTILEXR_BUILD_EP=OFF','-DTILEXR_BUILD_COLLECTIVES=OFF','-DTILEXR_BUILD_DIAGNOSTICS=OFF','-DTILEXR_BUILD_TESTS=OFF','-DTILEXR_BUILD_UNITEST=OFF','-DTILEXR_BUILD_MOONEP=OFF','-DTILEXR_BUILD_MOONEP_PLANNER=OFF','-DTILEXR_BUILD_RELAY=OFF','-DTILEXR_BUILD_CHECKER=OFF','-DTILEXR_BUILD_RDMA=ON']
run('runtime-configure',['cmake','-S',str(root/'runtime-source'),'-B',str(root/'runtime-build')]+flags)
run('runtime-build',['cmake','--build',str(root/'runtime-build'),'-j8'])
run('runtime-install',['cmake','--install',str(root/'runtime-build')])
for variant in ['control','global']:
 source=root/variant/'source/dispatch/dispatch-release';build=root/variant/'build';cpu=root/variant/'cpu-build'
 if variant=='global':
  run(variant+'-cpu-configure',['cmake','-S',str(source),'-B',str(cpu),'-DUBX_BUILD_ASCEND=OFF','-DBUILD_TESTING=ON','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_FLAGS=-fno-strict-aliasing'])
  run(variant+'-cpu-build',['cmake','--build',str(cpu),'-j8','--target','test_receiver_kernel','test_hardware_fixture_ep32'])
  run(variant+'-cpu-test',['ctest','--test-dir',str(cpu),'--output-on-failure','-R','receiver_kernel|hardware_fixture'])
 run(variant+'-configure',['cmake','-S',str(source),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release','-DUBX_BUILD_ASCEND=ON','-DUBX_BUILD_HARDWARE_TESTS=ON','-DBUILD_TESTING=OFF','-DTILEXR_INSTALL='+str(root/'runtime-install'),'-DUBX_BUILD_CLOCK_CALIBRATOR=OFF'])
 run(variant+'-build',['cmake','--build',str(build),'-j8'])
print(json.dumps({'built':True,'root':str(root),'npu_launched':False,'runtime_revision':'0797410beab1da40800611affc9c512cd42525ba'}))
'''
p=subprocess.run(ssh+['root@90.90.93.81','python3 -'],input=remote,text=True,capture_output=True,timeout=4200)
for line in p.stdout.splitlines():print(json.dumps(json.loads(line)),flush=True)
assert p.returncode==0,'Build failed; inspect stage logs before retry'
