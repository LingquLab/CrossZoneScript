import json, subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').iterdir() if 'deploy' in p.name and p.is_file() and p.suffix.lower() not in ['.pub','.old','.bak'] and p.name not in ['config','known_hosts','authorized_keys'] and p.stat().st_size<65536 and b'PRIVATE KEY' in p.read_bytes()]
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys: ssh+=['-i',str(key)]
files={p.name:p.read_text().replace('aclrtSetDevice(6)', 'aclrtSetDevice(1)').replace('aclrtResetDevice(6)', 'aclrtResetDevice(1)') for p in Path(__file__).with_name('official_hello').iterdir() if p.is_file()}
remote="""import json,subprocess,shlex
from pathlib import Path
root=Path('/root/ubx-control-ab-ep16-d23ffa56/official-hello')
root.mkdir(exist_ok=True)
for name,data in FILES.items(): (root/name).write_text(data)
commands=[['cmake','-S',str(root),'-B',str(root/'build'),'-DCMAKE_ASC_ARCHITECTURES=dav-3510'],['cmake','--build',str(root/'build'),'-j4']]
rows=[]
for i,cmd in enumerate(commands):
 shell='source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)
 p=subprocess.run(['bash','-lc',shell],capture_output=True,text=True,timeout=180)
 (root/('build-stage%d.log'%i)).write_text(p.stdout+p.stderr)
 rows.append({'stage':i,'exit':p.returncode,'diagnostics':(p.stdout+p.stderr).splitlines()[-6:]})
 if p.returncode: break
print(json.dumps({'build_exit':rows[-1]['exit'],'stages':rows,'npu_launched':False}))
""".replace('FILES',repr(files),1)
for host in ['90.90.93.81','90.90.93.83']:
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=remote,text=True,capture_output=True,timeout=150)
 assert p.returncode==0
 r=json.loads(p.stdout);r['host']=host;print(json.dumps(r))
 assert r['build_exit']==0
