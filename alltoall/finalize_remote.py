"""Collect bounded final evidence, then stop only this session's idle container."""
import json
from pathlib import Path
import subprocess

NAME='tilexr-cpu-b4f4660a-lr8'


def command(argv,timeout=30):
    return subprocess.check_output(argv,stderr=subprocess.PIPE,timeout=timeout).decode('utf-8')


inner='''import hashlib,json,os
from pathlib import Path
processes=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        comm=(p/'comm').read_text().strip()
        if comm in ('sst','sst.x','sstsim.x'): processes.append({'pid':int(p.name),'comm':comm})
    except OSError: pass
assert not processes,'Simulator still running; do not stop container'
result={'remaining_simulators':processes,'cases':[]}
for size in (512,516):
    root=Path('/work/lr8-source/lr8-8p-'+str(size)+'b')
    scenario=json.loads((root/'scenario.json').read_text())
    row={'bytes_per_peer':size,'rank_size':scenario['rank_size'],
         'elements_per_peer':scenario['elements_per_peer'],
         'kernel_sha256':scenario['kernel']['sha256'],'entry':scenario['kernel']['entry'],
         'runtime_commit':scenario['source_commit'],'tooling_commit':scenario['tooling_commit'],
         'runs':[]}
    for p in sorted((root/'runs').glob('*/run.json')):
        run=json.loads(p.read_text())
        row['runs'].append({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
            **{k:v for k,v in run.items() if k in ('status','returncode','error','simulation_time','elapsed_seconds','timeout_seconds')}})
    validation=root/'validation_summary.json'
    if validation.exists():
        obj=json.loads(validation.read_text())
        row['validation']={'path':str(validation),'sha256':hashlib.sha256(validation.read_bytes()).hexdigest(),
            'keys':list(obj)[:20] if isinstance(obj,dict) else [],
            **({k:v for k,v in obj.items() if k in ('status','rank_size','verified_ranks','ranks_verified','error')} if isinstance(obj,dict) else {})}
    result['cases'].append(row)
print(json.dumps(result,ensure_ascii=True))
'''
result=json.loads(command(['docker','exec',NAME,'python3','-c',inner]))
info,=json.loads(command(['docker','inspect',NAME]))
assert info['HostConfig']['Runtime']=='runc' and not info['HostConfig']['Devices'] and not info['HostConfig']['Privileged']
command(['docker','stop','--time','10',NAME],timeout=25)
info,=json.loads(command(['docker','inspect',NAME]))
assert not info['State']['Running']
result['container']={'name':NAME,'id':info['Id'],'running':False,'runtime':'runc','devices':info['HostConfig']['Devices'],
    'evidence_preserved':True}
Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/final-summary.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,ensure_ascii=True))
