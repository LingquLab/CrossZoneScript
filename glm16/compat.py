"""Locate model support in candidate images, using their actual import paths."""
import json
import subprocess

code = r'''
import importlib.util,json,pathlib
out={}
for package in ['vllm','vllm_ascend']:
 spec=importlib.util.find_spec(package)
 if spec is None:out[package]={'present':False};continue
 roots=[pathlib.Path(p) for p in (spec.submodule_search_locations or [])]
 record={'roots':[str(p) for p in roots],'model_markers':[],'mxfp4_paths':[]}
 for root in roots:
  for i,p in enumerate(root.rglob('*.py')):
   if i>=6000:break
   if p.stat().st_size>1024*1024:continue
   name=str(p.relative_to(root))
   if 'mxfp4' in name.lower():record['mxfp4_paths'].append(name)
   if 'GlmMoeDsaForCausalLM' in p.read_text(errors='replace'):record['model_markers'].append(name)
 record['model_markers']=record['model_markers'][:12]
 record['mxfp4_paths']=record['mxfp4_paths'][:12]
 out[package]=record
print(json.dumps(out))
'''
out=[]
for image in ['aa5e2bf3ec92','558855903609']:
    try:
        p=subprocess.run(['docker','run','--rm','--network','none','--entrypoint','python3',
                          image,'-c',code],capture_output=True,text=True,timeout=60)
        out.append({'image':image,'exit':p.returncode,
                    'support':json.loads(p.stdout) if p.returncode==0 else None})
    except Exception as e:
        out.append({'image':image,'error_class':type(e).__name__})
print(json.dumps({'images':out}))
