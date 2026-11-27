import json, subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').iterdir() if p.is_file() and p.suffix.lower() not in ['.pub','.old','.bak'] and p.name not in ['config','known_hosts','authorized_keys'] and p.stat().st_size<65536 and b'PRIVATE KEY' in p.read_bytes()]
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys: ssh+=['-i',str(key)]
source=Path(__file__).with_name('empty_launch.cpp').read_text()
remote="""import json,subprocess
from pathlib import Path
root=Path('/root/ubx-control-ab-ep16-d23ffa56/empty-launch')
root.mkdir(exist_ok=True)
(root/'main.cpp').write_text(SOURCE)
cmd='source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && g++ -std=c++14 '+str(root/'main.cpp')+' -I/usr/local/Ascend/cann/include -I/usr/local/Ascend/cann/include/experiment -L/usr/local/Ascend/cann/lib64 -Wl,-rpath,/usr/local/Ascend/cann/lib64 -lascendcl -lruntime -o '+str(root/'empty_launch')
p=subprocess.run(['bash','-lc',cmd],capture_output=True,text=True,timeout=120)
(root/'build.log').write_text(p.stdout+p.stderr)
print(json.dumps({'build_exit':p.returncode,'errors':p.stderr.splitlines()[:8],'npu_launched':False}))
""".replace('SOURCE',repr(source),1)
for host in ['90.90.93.84','90.90.93.188']:
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=remote,text=True,capture_output=True,timeout=150)
 assert p.returncode==0
 r=json.loads(p.stdout);r['host']=host;print(json.dumps(r))
 assert r['build_exit']==0
