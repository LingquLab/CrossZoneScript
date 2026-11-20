"""Retrieve selected errors from the already-failed 516B SST run."""
import json
from pathlib import Path
import subprocess


remote = r'''import json,re
from pathlib import Path
case=Path('/work/lr8-1/lr8-8p-516b')
r={'case':str(case),'runs':[],'diagnostics':{}}
for run in sorted((case/'runs').glob('*')):
 p=run/'run.json'
 row={'path':str(run)}
 if p.is_file():
  data=json.loads(p.read_text())
  row['available_fields']=list(data)
  for key in ['status','returncode','error','reason','validation','timed_out','elapsed_seconds']:
   if key in data:
    value=data[key]
    if isinstance(value,(str,int,float,bool)) or value is None: row[key]=str(value)[:1200] if isinstance(value,str) else value
    elif key=='validation' and isinstance(value,dict):
     row[key]={k:v for k,v in value.items() if k in ['status','guards','kernel_status','rank_count','die_address_ownership_verified','error'] and isinstance(v,(str,int,float,bool))}
 r['runs'].append(row)
logs=[case/'runner.log',*sorted((case/'runs').glob('*/sim.log'))]
for path in logs:
 if not path.is_file(): continue
 matches=[]
 with path.open(errors='replace') as stream:
  for number,line in enumerate(stream,1):
   if re.search(r'FATAL|ERROR|Error|error|failed|No module|No such|cannot|not found|undefined symbol|Simulation is complete',line) and len(line)<1400:
    matches.append({'line':number,'text':line.rstrip()})
 r['diagnostics'][str(path)]=matches[:4]+matches[-4:] if len(matches)>8 else matches
print(json.dumps(r,ensure_ascii=True))
'''
p = subprocess.run(['ssh', '-T', '-i', 'C:/Users/z00813871/.ssh/id_ed25519',
    '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ConnectTimeout=12', 'root@141.61.94.26',
    'timeout 60 docker exec -i tilexr-matrix-acc8c509-lr8-1 python3 -'],
    input=remote.encode(), capture_output=True, timeout=75)
if p.returncode:
    raise RuntimeError('Retained SST error inspection failed')
print(json.dumps(json.loads(p.stdout), ensure_ascii=True))
