import hashlib,json,shlex,subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
root='/root/ubx-mem-relay-d23ffa56';runtime='/root/ubx-dispatch-global-ab-d23ffa56/runtime-install';inputs='/root/ubx-token-merge-matrix-d23ffa56/inputs/routes-bs32.bin'
# 只复制本次任务构建产物、独立固定运行库和已确认输入，不启动源机器NPU。
p=subprocess.run(ssh+['root@90.90.93.81','tar -cf - -C /root ubx-mem-relay-d23ffa56 ubx-dispatch-global-ab-d23ffa56/runtime-install ubx-token-merge-matrix-d23ffa56/inputs/routes-bs32.bin'],capture_output=True,timeout=180);assert p.returncode==0
payload=p.stdout
q=subprocess.run(ssh+['root@90.90.93.86','python3 -c '+shlex.quote("from pathlib import Path;assert not Path('/root/ubx-mem-relay-d23ffa56').exists(),'Existing deployment; inspect before retry'")],capture_output=True,timeout=30);assert q.returncode==0
q=subprocess.run(ssh+['root@90.90.93.86','tar -xf - -C /root'],input=payload,capture_output=True,timeout=180);assert q.returncode==0
remote=r'''
import hashlib,json,shlex,subprocess
from pathlib import Path
root=Path('/root/ubx-mem-relay-d23ffa56');runtime=Path('/root/ubx-dispatch-global-ab-d23ffa56/runtime-install');routes=Path('/root/ubx-token-merge-matrix-d23ffa56/inputs/routes-bs32.bin')
receipt=json.loads((root/'build-receipt.json').read_text());assert receipt['revision']=='2a641ac4a647dc7232fcbfb70676082058143e70'
assert hashlib.sha256((root/'build/mem_group.o').read_bytes()).hexdigest()==receipt['kernel_sha256']=='3b5d7db450d5dca304c60f830b08c17c21078c055067728f839ecd511211ff08'
assert hashlib.sha256(routes.read_bytes()).hexdigest()=='2a58c479564c82f5185d42bfa3608a5e9f2128e0125c921a1d6ab4ec18f63b24'
cmd=['python3',str(root/'source/dispatch/stage2/mem-relay-bench/run.py'),'--build',str(root/'build'),'--runtime',str(runtime),'--routes',str(routes),'--output',str(root/'smoke-86'),'--port','35000','--warmup','1','--measured','1']
p=subprocess.run(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1 && '+shlex.join(cmd)],capture_output=True,text=True,timeout=1300)
(root/'smoke-86.stdout.log').write_text(p.stdout);(root/'smoke-86.stderr.log').write_text(p.stderr)
result=dict(host='90.90.93.86',phase='smoke',exit=p.returncode,revision=receipt['revision'],kernel_sha256=receipt['kernel_sha256'])
if p.returncode==0:result['result']=json.loads((root/'smoke-86/result.json').read_text())
else:
 result['runner_tail']=p.stderr.splitlines()[-5:];result['workers']=[dict(rank=i,tail=(root/('smoke-86/rank%d.log'%i)).read_text(errors='replace').splitlines()[-5:]) for i in [0,1,8] if (root/('smoke-86/rank%d.log'%i)).is_file()]
print(json.dumps(result))
'''
p=subprocess.run(ssh+['root@90.90.93.86','python3 -'],input=remote,text=True,capture_output=True,timeout=1400)
Path('relay-86-smoke.stdout.log').write_text(p.stdout);Path('relay-86-smoke.stderr.log').write_text(p.stderr);assert p.returncode==0
r=json.loads(p.stdout);print(json.dumps(r));assert r['exit']==0,'Smoke failed; inspect saved evidence'
