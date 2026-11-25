"""Inspect candidate runtime packages without mounting or using NPU devices."""
import json
import subprocess

probe = r'''
import importlib.metadata as m,json,pathlib
out={'packages':{}}
for k in ['vllm','vllm-ascend','torch','torch-npu']:
 try:out['packages'][k]=m.version(k)
 except Exception as e:out['packages'][k]=type(e).__name__
out['cann_versions']={}
for s in ['/usr/local/Ascend/ascend-toolkit/latest/version.cfg','/usr/local/Ascend/cann/version.cfg','/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/ascend_toolkit_install.info']:
 p=pathlib.Path(s)
 if p.is_file():out['cann_versions'][s]=[x for x in p.read_text().splitlines() if 'version' in x.lower()][:8]
try:
 p=pathlib.Path(m.distribution('vllm').locate_file('vllm/model_executor/models'))
 out['glm_modules']=[x.name for x in p.glob('*glm*')]
except Exception:pass
print(json.dumps(out))
'''
out=[]
for image in ['558855903609','aa5e2bf3ec92','64286c338a6e']:
    exists = subprocess.run(['docker','image','inspect','--format','{{.Id}}',image],
                            capture_output=True,text=True,timeout=12)
    if exists.returncode:
        continue
    rec={'image':exists.stdout.strip()}
    try:
        p=subprocess.run(['docker','run','--rm','--network','none','--entrypoint','python3',
                          image,'-c',probe],capture_output=True,text=True,timeout=45)
        rec['metadata']=json.loads(p.stdout) if p.returncode==0 else {'exit':p.returncode}
    except Exception as e:
        rec['error_class']=type(e).__name__
    out.append(rec)
print(json.dumps({'images':out}))
