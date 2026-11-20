"""Read retained SST model settings and bounded Die/block allocation evidence."""
import json
from pathlib import Path
import subprocess

NAME='tilexr-cpu-b4f4660a-lr8'
info,=json.loads(subprocess.check_output(['docker','inspect',NAME],timeout=15))
assert not info['State']['Running'] and info['HostConfig']['Runtime']=='runc' and not info['HostConfig']['Devices']
subprocess.run(['docker','start',NAME],capture_output=True,check=True,timeout=30)
inner='''import hashlib,json,re
from pathlib import Path
import yaml
root=Path('/work/lr8-source/lr8-64p-512b')
snapshot=json.loads((root/'environment.snapshot.json').read_text())
result={'settings':snapshot['settings'],'resources':{k:v for k,v in snapshot['resources'].items() if k=='installed_model.py'},'model_programs':[],'config_fields':[]}
files=[v for v in snapshot['runtime_files'].values() if str(v['path']).endswith('.py') and Path(v['path']).name in ('host_simulator.py','component_param.py','UBusSim_arch.py')]
files.append(snapshot['resources']['installed_model.py'])
for row in files:
 p=Path(row['path'])
 if not p.is_file():continue
 text=p.read_text()
 lines=[{'line':i+1,'text':s.strip()[:550]} for i,s in enumerate(text.splitlines()) if re.search(r'block_dim|aiv_num|die_id|ddie|core_num|dieNum|die_num|die_idx',s,re.I)]
 result['model_programs'].append({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'matching_line_count':len(lines),'lines':lines[:8]})
def collect(value,path):
 if isinstance(value,dict):
  for key,item in value.items():
   if str(key).startswith('chip') and str(key) not in ('chip0','chip_num','chip_nums'):continue
   collect(item,path+'/'+str(key))
 elif isinstance(value,list):
  for i,item in enumerate(value[:2]):collect(item,path+'/'+str(i))
 elif re.search(r'die|core|block|chip_num|aiv',path,re.I) and len(result['config_fields'])<80:
  result['config_fields'].append({'path':path,'value':value})
for relative in ('v310_config/workload_config.yml','v310_config/udie_noc_config.yml','UBusSim_config/UBusSim_simulator_config.yml'):
 p=root/relative
 if p.is_file():collect(yaml.safe_load(p.read_text()),relative)
lib=Path(snapshot['settings']['library_dir'])/'libDavinciSimulator.so'
result['decoder']={'path':str(lib),'sha256':hashlib.sha256(lib.read_bytes()).hexdigest()}
print(json.dumps(result,ensure_ascii=True))
'''
try:
    proc=subprocess.run(['docker','exec',NAME,'python3','-c',inner],capture_output=True,timeout=45,check=True)
    result=json.loads(proc.stdout)
finally:
    subprocess.run(['docker','stop','--time','10',NAME],capture_output=True,check=True,timeout=25)
print(json.dumps(result,ensure_ascii=True))
