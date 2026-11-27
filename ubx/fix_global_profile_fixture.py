# Build the fixed control/global profiling variants on the authorized server.
import json, subprocess, tempfile, shlex
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and p.stat().st_size<65536 and b'PRIVATE KEY' in p.read_bytes()]
assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
checkout=Path('D:/code-project/TileXR-shared-off25-cad5b90')
revision='48ab0bbf69672ef7faa673c4a6bbfad90f375b31'
p=subprocess.run(['git','-C',str(checkout),'fetch','origin','codex/ubx-global-ep16-ab'],capture_output=True,timeout=120)
assert p.returncode==0
root='/root/ubx-control-profile-ep32-d23ffa56/global'
with tempfile.TemporaryFile() as archive:
 p=subprocess.run(['git','-C',str(checkout),'archive',revision,'tests/ep/ubx_full_intra/host.cpp','tests/ep/ubx_full_intra/README.md'],stdout=archive,stderr=subprocess.PIPE,timeout=60)
 assert p.returncode==0
 archive.seek(0)
 p=subprocess.run(ssh+['root@90.90.93.81','tar -xf - -C '+shlex.quote(root+'/source')],stdin=archive,capture_output=True,timeout=60)
 assert p.returncode==0
remote="""import json,subprocess,hashlib
from pathlib import Path
root=Path('/root/ubx-control-profile-ep32-d23ffa56/global')
kernel=root/'on/lib/tilexr/kernels/tilexr_ep_ubx_kernel.o'
before=hashlib.sha256(kernel.read_bytes()).hexdigest()
for i,cmd in enumerate([['cmake','--build',str(root/'fixture-on-build'),'-j8'],['cmake','--install',str(root/'fixture-on-build')]]):
 import shlex
 p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=180)
 (root/('oracle-fix-stage%d.log'%i)).write_text(p.stdout+p.stderr)
 print(json.dumps({'stage':i,'exit':p.returncode,'tail':(p.stdout+p.stderr).splitlines()[-3:]}),flush=True)
 if p.returncode:raise SystemExit(1)
assert hashlib.sha256(kernel.read_bytes()).hexdigest()==before
r=json.loads((root/'receipt.json').read_text());r['fixture_revision']='48ab0bbf69672ef7faa673c4a6bbfad90f375b31';r['kernel_unchanged']=True;(root/'receipt.json').write_text(json.dumps(r))
print(json.dumps({'kernel_unchanged':True,'npu_launched':False}))
"""
p=subprocess.run(ssh+['root@90.90.93.81','python3 -'],input=remote,text=True,capture_output=True,timeout=420)
for line in p.stdout.splitlines():print(json.dumps(json.loads(line)),flush=True)
assert p.returncode==0
