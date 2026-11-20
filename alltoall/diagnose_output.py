"""Use the existing retained-output decoder, then export only mismatch summaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
from ssh_transport import run

REPORT=Path('D:/project/tilexr-sim-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
TOOL=Path('D:/project/tileXR-worktrees/sst-b4f4660a-tool')
request=json.loads((REPORT/'unitest-129/run/request.json').read_text(encoding='utf-8'))
request['inventory']=str(REPORT/'inventory.json')
flags=('inventory','cluster','host','hostname','container','container_id','image_id','sources','output',
       'runtime_commit','source_lock_sha256','cann_root','driver_root','cann_env','cann_env_sha256',
       'ubus_library','ubus_sha256','sim_variant','environment_report','suite_output','rank_size','elements_per_peer')
argv=[sys.executable,str(TOOL/'scripts/run_alltoall_sim_kernel.py'),'diagnose-output']
for key in flags:
    argv+=['--'+key.replace('_','-'),str(request[key])]
argv+=['--timeout','300','--local-output',str(REPORT/'diagnose-output-129')]
env=os.environ.copy()
env.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
with (REPORT/'diagnose-output-129.log').open('wb') as log:
    proc=subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,env=env,timeout=350)
assert proc.returncode==0,'Output diagnosis failed; inspect green-local report'
inner='''import json
from pathlib import Path
case=Path('/work/lr8-source/lr8-8p-516b')
paths=list(case.glob('output-diagnosis-*.json'))
assert len(paths)==1
doc=json.loads(paths[0].read_text())
summary={'scope':doc['scope'],'report':str(paths[0]),'ranks':[]}
for row in doc['ranks']:
    rank={'rank':row['rank'],'guards_ok':row['guards_ok'],'status_zero':[x['zero'] for x in row['status']],
          'status_nonzero_words':[[[i,x] for i,x in enumerate(s['words']) if x] for s in row['status']],
          'peers':[]}
    for peer in row['peers']:
        item={k:v for k,v in peer.items() if k in ('source_rank','input_offset_mod512','received_address_mod512','output_address_mod512')}
        for field in ('output','received','received_vs_output'):
            if field in peer: item[field]={k:v for k,v in peer[field].items() if k in ('mismatch_bytes','first_byte','ranges','ranges_truncated')}
        rank['peers'].append(item)
    summary['ranks'].append(rank)
print(json.dumps(summary,ensure_ascii=True))
'''
outer='import subprocess\np=subprocess.run('+repr(['docker','exec','tilexr-cpu-b4f4660a-lr8','python3','-c',inner])+',capture_output=True,timeout=30)\nassert p.returncode==0\nprint(p.stdout.decode())'
print(json.dumps(run(outer),ensure_ascii=True))
