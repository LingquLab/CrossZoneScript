import json,shlex,subprocess,tempfile
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
root='/root/ubx-rdma-group8-overlap-d23ffa56'
init='from pathlib import Path;p=Path('+repr(root)+');assert not p.exists(),"Inspect existing deployment before retry";p.mkdir()'
p=subprocess.run(ssh+['root@90.90.93.86','python3 -'],input=init,capture_output=True,text=True,timeout=25);assert p.returncode==0
with tempfile.TemporaryFile() as archive:
 p=subprocess.run(ssh+['root@90.90.93.83','tar -czf - -C '+shlex.quote(root)+' control group8 inputs build-receipt.json'],stdout=archive,stderr=subprocess.PIPE,timeout=180);assert p.returncode==0
 archive.seek(0);p=subprocess.run(ssh+['root@90.90.93.86','tar -xzf - -C '+shlex.quote(root)],stdin=archive,capture_output=True,timeout=180);assert p.returncode==0
verify='import json,hashlib;from pathlib import Path;r=Path('+repr(root)+');d=json.loads((r/"build-receipt.json").read_text());assert d["revision"]=="568c1b306aa66eee742ed6c6e633ceccd9ea4389";assert all(hashlib.sha256((r/n).read_bytes()).hexdigest()==h for n,h in d["artifacts"].items());print(json.dumps(dict(deployed=True,host="90.90.93.86",files=len(d["artifacts"]))))'
p=subprocess.run(ssh+['root@90.90.93.86','python3 -'],input=verify,text=True,capture_output=True,timeout=90);assert p.returncode==0;print(p.stdout,flush=True)
# 控制器读取执行目录args.json，Bridge须传variant/profile以符合前置参数约束。
exec(compile(Path(__file__).with_name('rdma_group8_overlap_controller.py').read_text(),'rdma-group8-controller','exec'),{'__name__':'__main__','__file__':str(Path(__file__).with_name('rdma_group8_overlap_controller.py'))})
