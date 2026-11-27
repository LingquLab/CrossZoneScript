# Build the requested UBX A/B variants and pinned runtime on the test server.
import json,subprocess,tempfile,shlex
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and p.stat().st_size<65536 and b'PRIVATE KEY' in p.read_bytes()]
assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
checkout=Path(tempfile.mkdtemp(prefix='ubx-dispatch-global-fix-'))
p=subprocess.run(['git','clone','--depth','1','--branch','codex/dispatch-global-routes-ab','https://github.com/qizhiliu0639/UBX.git',str(checkout/'ubx')],capture_output=True,timeout=180);assert p.returncode==0
revision='a047c18ee4d074e0d4f92294c29b049410eb0453'
with tempfile.TemporaryFile() as archive:
 p=subprocess.run(['git','-C',str(checkout/'ubx'),'archive',revision,'dispatch/dispatch-release'],stdout=archive,stderr=subprocess.PIPE,timeout=120);assert p.returncode==0
 archive.seek(0);p=subprocess.run(ssh+['root@90.90.93.81','tar -xf - -C /root/ubx-dispatch-global-ab-d23ffa56/global/source'],stdin=archive,capture_output=True,timeout=120);assert p.returncode==0
remote="import json,subprocess,shlex,shutil,hashlib\nfrom pathlib import Path\nroot=Path('/root/ubx-dispatch-global-ab-d23ffa56')\nfor name in ['spdlog','picojson']:shutil.copytree(Path('/root/ubx-relay-bs32-84da56a-exclusive/runtime/3rdparty')/name,root/'runtime-source/3rdparty'/name,dirs_exist_ok=True)\ndef run(label,cmd,timeout=600):\n p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=timeout)\n (root/(label+'.log')).write_text(p.stdout+p.stderr)\n print(json.dumps({'stage':label,'exit':p.returncode,'tail':(p.stdout+p.stderr).splitlines()[-4:]}),flush=True)\n if p.returncode:raise SystemExit(1)\nfor variant in ['global']:\n source=root/variant/'source/dispatch/dispatch-release';build=root/variant/'build';cpu=root/variant/'cpu-build'\n if variant=='global':\n  run(variant+'-cpu-configure',['cmake','-S',str(source),'-B',str(cpu),'-DUBX_BUILD_ASCEND=OFF','-DBUILD_TESTING=ON','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_FLAGS=-fno-strict-aliasing'])\n  run(variant+'-cpu-build',['cmake','--build',str(cpu),'-j8','--clean-first','--target','test_receiver_kernel','test_hardware_fixture_ep32'])\n  run(variant+'-cpu-test',['ctest','--test-dir',str(cpu),'--output-on-failure','-R','receiver_kernel|hardware_fixture'])\n run(variant+'-configure',['cmake','-S',str(source),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release','-DUBX_BUILD_ASCEND=ON','-DUBX_BUILD_HARDWARE_TESTS=ON','-DBUILD_TESTING=OFF','-DTILEXR_INSTALL='+str(root/'runtime-install'),'-DUBX_BUILD_CLOCK_CALIBRATOR=OFF'])\n run(variant+'-build',['cmake','--build',str(build),'-j8'])\nprint(json.dumps({'built':True,'root':str(root),'npu_launched':False,'runtime_revision':'0797410beab1da40800611affc9c512cd42525ba'}))\n"
p=subprocess.run(ssh+['root@90.90.93.81','python3 -'],input=remote,text=True,capture_output=True,timeout=4200)
for line in p.stdout.splitlines():print(json.dumps(json.loads(line)),flush=True)
assert p.returncode==0,'Build failed; inspect stage logs before retry'
