"""Collect a small isolated NPU tensor check after confirming no existing workload."""
import glob
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path('/home/glm16-09b20820')
ROOT.mkdir(exist_ok=True)
LOGS = ROOT / ('device-probe-' + str(time.time_ns()))
LOGS.mkdir()
IMAGE = 'sha256:5588559036095d339c1cfb48c16d0227b841355490d8c0cb4e6730759f9c6674'
NAME = 'glm16-09b20820-device-probe'
out = {'log_directory': str(LOGS)}
gate = subprocess.run(['/usr/local/sbin/npu-smi', 'info'], capture_output=True, text=True, timeout=65)
table = gate.stdout.split('Process id', 1)[-1]
idle = (gate.returncode == 0 and 'Process id' in gate.stdout
        and table.count('No running processes found') == 8
        and not re.search(r'^\|\s*\d+\s*\|\s*\d+\s*\|', table, re.M))
out['idle_before_probe'] = idle
if not idle:
    print(json.dumps(out))
    raise SystemExit(0)
existing = subprocess.run(['docker', 'container', 'inspect', NAME], capture_output=True, timeout=12)
assert existing.returncode != 0, 'task_probe_container_already_exists'
code = r'''
import json,time,subprocess,pathlib,traceback
out={};started=time.monotonic()
try:
 out['boundary']='import_torch'
 import torch,torch_npu
 out['boundary']='device_count'
 out['device_count']=torch.npu.device_count()
 out['torch_version']=torch.__version__
 assert out['device_count']==8
 checks=[]
 for i in range(8):
  out['boundary']=f'set_device_{i}'
  torch.npu.set_device(i)
  out['boundary']=f'allocate_tensor_{i}'
  value=torch.ones(1,device=f'npu:{i}')
  out['boundary']=f'read_tensor_{i}'
  checks.append({'device':i,'value':float(value.item())})
  del value
  torch.npu.empty_cache()
 out['tensor_checks']=checks
 out['boundary']='completed'
except Exception as e:
 out['error_class']=type(e).__name__
 out['error_message']=str(e).splitlines()[:8]
 pathlib.Path('/experiment/python-exception.log').write_text(traceback.format_exc())
 try:
  import acl
  recent=getattr(acl,'get_recent_err_msg',None)
  if recent:out['acl_recent_error']=str(recent())[:800]
 except Exception:pass
 import re
 out['error_codes']=re.findall(r'\b(?:ERR\d+|\d{6}|[A-Z][A-Z0-9_]{8,})\b',str(e))[-8:]
out['elapsed_seconds']=round(time.monotonic()-started,3)
print('GLM_PROBE_RESULT='+json.dumps(out),flush=True)
'''
cmd = ['docker', 'create', '--name', NAME, '--network', 'none', '--shm-size=4g',
       '--pids-limit', '2048', '--cap-add', 'SYS_NICE', '--ulimit', 'memlock=-1:-1',
       '-e', 'ASCEND_RT_VISIBLE_DEVICES=0,1,2,3,4,5,6,7', '-e', 'ASCEND_GLOBAL_LOG_LEVEL=2',
       '-e', 'ASCEND_PROCESS_LOG_PATH=/experiment/ascend-logs',
       '--mount', f'type=bind,src={LOGS},dst=/experiment']
for p in sorted(set(glob.glob('/dev/davinci*') + glob.glob('/dev/hisi*')
                    + glob.glob('/dev/uburma/*') + glob.glob('/dev/ummu*'))):
    if Path(p).is_char_device():
        cmd += ['--device', p]
for p in ['/usr/local/Ascend/driver', '/usr/local/dcmi', '/usr/lib64/urma', '/etc/hixlep', '/etc/hccn.conf']:
    if Path(p).exists():
        cmd += ['--mount', f'type=bind,src={p},dst={p},readonly']
for pattern in ['/usr/lib64/libhrn5*', '/usr/lib64/liburma*', '/usr/lib64/libummu*']:
    for p in glob.glob(pattern):
        if Path(p).is_file():
            cmd += ['--mount', f'type=bind,src={p},dst={p},readonly']
cmd += ['--entrypoint', 'python3', IMAGE, '-c', code]
created = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
(LOGS / 'create.stderr.log').write_text(created.stderr)
out['container_create_exit'] = created.returncode
if created.returncode == 0:
    cid = created.stdout.strip()
    assert re.fullmatch('[0-9a-f]{64}', cid)
    try:
        p = subprocess.run(['docker', 'start', '-a', cid], capture_output=True, text=True, timeout=90)
        (LOGS / 'stdout.log').write_text(p.stdout)
        (LOGS / 'stderr.log').write_text(p.stderr)
        out['probe_exit'] = p.returncode
        for line in p.stdout.splitlines():
            if line.startswith('GLM_PROBE_RESULT='):
                out['observation'] = json.loads(line.split('=', 1)[1])
    except subprocess.TimeoutExpired:
        out['probe_timeout_seconds'] = 90
    finally:
        cleaned = subprocess.run(['docker', 'rm', '-f', cid], capture_output=True, timeout=20)
        out['owned_container_removed'] = cleaned.returncode == 0
(LOGS / 'result.json').write_text(json.dumps(out))
(ROOT / 'device-probe-latest.json').write_text(json.dumps(out))
print(json.dumps(out))
