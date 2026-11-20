"""Inspect the existing CPU-only SST container; do not touch NPU devices."""
import json
from pathlib import Path
import subprocess

NAME='tilexr-cpu-b4f4660a-lr8'
CID='1f98e70b00ed8b171fea3b06152a7fe9507a2270e9dee05c72015af5b995c2f4'


def cmd(argv,timeout=40):
    return subprocess.check_output(argv,stderr=subprocess.PIPE,timeout=timeout).decode('utf-8')


info,=json.loads(cmd(['docker','inspect',NAME]))
assert info['Id']==CID and info['HostConfig']['Runtime']=='runc'
assert not info['HostConfig']['Devices'] and not info['HostConfig']['Privileged']
assert not info['State']['Running'],'Container busy; do not share active resources'
cmd(['docker','start',NAME])
inner='''import glob,hashlib,json,os,subprocess
from pathlib import Path
root=Path('/opt/sim/Ascend/cann-9.1.0')
assert not glob.glob('/dev/davinci*') and not glob.glob('/dev/devmm*') and not glob.glob('/dev/hisi_hdc*')
def query(argv):
 p=subprocess.run(argv,capture_output=True,timeout=30)
 return {'exit_code':p.returncode,'stdout':p.stdout.decode('utf-8','replace')[:5000],'stderr_bytes':len(p.stderr)}
result={'cann_root':str(root),'npu_devices':[], 'compiler':query([str(root/'tools/bisheng_compiler/bin/bisheng'),'--version'])}
result['sst']=query(['/opt/sim/local/sstcore/libexec/sstsim.x','--version'])
result['toolkit_metadata']=[]
for p in [root/'version.info',root/'aarch64-linux/ascend_toolkit_install.info',root/'compiler/version.info']:
 if p.is_file(): result['toolkit_metadata'].append({'path':str(p),'text':p.read_text(errors='replace')[:2500]})
targets={'shmem.h','shmem_device_simt_instance.h','device_functions.h','libshmem.so','asc_simd.h','kernel_operator.h'}
result['headers_and_libraries']=[]
for scope in ('/opt/sim/Ascend/cann-9.1.0','/opt/sim/operator','/opt/sim/build-dispatch','/usr/local/Ascend'):
 for base,dirs,files in os.walk(scope):
  dirs[:]=[x for x in dirs if x not in ('python','python3.11','python3.12','site-packages','__pycache__')]
  for name in targets.intersection(files):
   p=Path(base)/name
   result['headers_and_libraries'].append({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
  if len(result['headers_and_libraries'])>45: break
result['work_simulator_root_exists']=Path('/work').is_dir()
helptext=subprocess.run([str(root/'tools/bisheng_compiler/bin/bisheng'),'--help'],capture_output=True,timeout=20).stdout.decode('utf-8','replace')
result['compiler_help_relevant']=[s.strip() for s in helptext.splitlines() if any(x in s for x in ('npu-arch','cce-aicore','fatobj','auto-infer'))][:20]
print(json.dumps(result,ensure_ascii=True))
'''
try:
    result=json.loads(cmd(['docker','exec',NAME,'bash','-c','source /opt/sim/cann-env.sh; exec python3 -c "$1"','bash',inner],timeout=90))
finally:
    cmd(['docker','stop','--time','10',NAME])
result['container']={'name':NAME,'id':CID,'runtime':'runc','stopped':True}
print(json.dumps(result,ensure_ascii=True))
