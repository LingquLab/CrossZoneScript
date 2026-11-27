from pathlib import Path
import json
p=Path('D:/code-project/harness/plugins/cross-zone-development/.state/d23ffa56-8956-47d1-b942-28b714bd37b8/runs/ubx-token-matrix-split-check/1/execution/stdout.log')
print(json.dumps({'controller_stdout_tail':p.read_text(encoding='utf-8',errors='replace')[-10000:]}))
import concurrent.futures,json,subprocess
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
remote=r'''
import json
from pathlib import Path
root=Path('/root/ubx-token-merge-matrix-d23ffa56');case=max(root.glob('ablation-*'),key=lambda p:p.stat().st_mtime);r=json.loads((case/'result.json').read_text());rows=[]
for d in sorted(case.glob('rank-*'))[:3]:
 records=d/'records';rank=int(d.name.split('-')[1]);row=dict(rank=rank,summaries=len(list(records.glob('*.summary.json'))),clocks=len(list(records.glob('*.clock.json'))),captures=len(list(records.glob('*.records.bin'))),stderr_tail=(d/'stderr.log').read_text(errors='replace').splitlines()[-5:],stdout_tail=(d/'stdout.log').read_text(errors='replace').splitlines()[-1:])
 rows.append(row)
print(json.dumps(dict(run_id=case.name,status=r['status'],timed_out=r['timed_out'],network=r['network_accept_local'],ranks=rows)))
'''
def one(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=remote,text=True,capture_output=True,timeout=75);Path('token-failure-'+host+'.stderr.log').write_text(p.stderr);assert p.returncode==0;r=json.loads(p.stdout);r['host']=host;return r
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for r in pool.map(one,['90.90.93.81','90.90.93.83']):print(json.dumps(r),flush=True)
