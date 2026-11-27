# Build the fixed control/global profiling variants on the authorized server.
import json, subprocess, tempfile, shlex
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and p.stat().st_size<65536 and b'PRIVATE KEY' in p.read_bytes()]
assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
checkout=Path('D:/code-project/TileXR-shared-off25-cad5b90')
variants=[('control','6cb5fceaf3a282a8d6dfb43acf7fb17b14651396','codex/ubx-control-ep16-ab'),('global','cddc120156074039f56683c4e5551012e7df75d9','codex/ubx-global-ep16-ab')]
for variant,revision,branch in variants:
 p=subprocess.run(['git','-C',str(checkout),'fetch','origin',branch],capture_output=True,timeout=120)
 assert p.returncode==0,'Source fetch failed'
 root='/root/ubx-control-profile-ep32-d23ffa56/'+variant
 setup='from pathlib import Path; p=Path('+repr(root+'/source')+'); assert not p.exists(), "Source already staged: inspect existing build before retry"; p.mkdir(parents=True)'
 p=subprocess.run(ssh+['root@90.90.93.81','python3 -'],input=setup,text=True,capture_output=True,timeout=20)
 assert p.returncode==0,'Source staging state failed'
 with tempfile.TemporaryFile() as archive:
  p=subprocess.run(['git','-C',str(checkout),'archive',revision],stdout=archive,stderr=subprocess.PIPE,timeout=60)
  assert p.returncode==0
  archive.seek(0)
  p=subprocess.run(ssh+['root@90.90.93.81','tar -xf - -C '+shlex.quote(root+'/source')],stdin=archive,capture_output=True,timeout=120)
  assert p.returncode==0,'Source transfer failed'
 remote=r'''import json,subprocess,shlex,shutil
from pathlib import Path
root=Path(ROOT)
retained=Path('/root/ubx-relay-bs32-84da56a-exclusive/runtime/3rdparty')
for name in ['spdlog','picojson']:
 shutil.copytree(retained/name,root/'source/3rdparty'/name,dirs_exist_ok=True)
flags=['-DCMAKE_INSTALL_PREFIX='+str(root/'on'),'-DTILEXR_BUILD_EP=ON','-DTILEXR_BUILD_RDMA=ON','-DTILEXR_BUILD_DIAGNOSTICS=ON','-DTILEXR_BUILD_TESTS=OFF','-DTILEXR_BUILD_UNITEST=OFF','-DTILEXR_BUILD_COLLECTIVES=OFF','-DTILEXR_BUILD_MOONEP=OFF','-DTILEXR_BUILD_MOONEP_PLANNER=OFF','-DTILEXR_BUILD_RELAY=OFF','-DTILEXR_BUILD_CHECKER=OFF','-DTILEXR_EP_ENABLE_PROFILING=ON','-DTILEXR_DIAGNOSTICS_SOC_TYPE=Ascend950','-DTILEXR_EP_UBX_FANOUT_COPIES=OFF','-DTILEXR_EP_UBX_PACKET_TRACE=OFF']
commands=[['cmake','-S',str(root/'source'),'-B',str(root/'on-build')]+flags,['cmake','--build',str(root/'on-build'),'-j8'],['cmake','--install',str(root/'on-build')],['cmake','-S',str(root/'source/tests/ep/ubx_full_intra'),'-B',str(root/'fixture-on-build'),'-DTILEXR_HOST_PREFIX='+str(root/'on'),'-DCMAKE_INSTALL_PREFIX='+str(root/'on'),'-DASCEND_HOME_PATH=/usr/local/Ascend/cann','-DTILEXR_FIXTURE_BUILD_ISOLATED=OFF','-DTILEXR_FIXTURE_BARRIER_PROFILING=OFF'],['cmake','--build',str(root/'fixture-on-build'),'-j8'],['cmake','--install',str(root/'fixture-on-build')]]
for i,cmd in enumerate(commands):
 p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=420)
 (root/('stage%d.log'%i)).write_text(p.stdout+p.stderr)
 print(json.dumps({'variant':VARIANT,'stage':i,'exit':p.returncode,'diagnostics':(p.stdout+p.stderr).splitlines()[-3:]}),flush=True)
 if p.returncode:raise SystemExit(1)
(root/'receipt.json').write_text(json.dumps({'revision':REVISION,'profiling':True,'variant':VARIANT,'npu_launched':False}))
'''.replace('ROOT',repr(root)).replace('VARIANT',repr(variant)).replace('REVISION',repr(revision))
 p=subprocess.run(ssh+['root@90.90.93.81','python3 -'],input=remote,text=True,capture_output=True,timeout=2600)
 for line in p.stdout.splitlines():print(json.dumps(json.loads(line)),flush=True)
 assert p.returncode==0,'Server build failed; inspect saved stages before retry'
