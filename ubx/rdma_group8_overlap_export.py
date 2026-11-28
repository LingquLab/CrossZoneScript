import hashlib,json,subprocess,zipfile
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and b'PRIVATE KEY' in p.read_bytes()];assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for k in keys:ssh+=['-i',str(k)]
out=Path('D:/work/UBX攻关/UBX_Control/tilexr-ep32-rdma-group8-overlap-code-profiling.zip');assert not out.exists()
remote=r'''
import io,json,sys,zipfile
from pathlib import Path
root=Path('/root/ubx-rdma-group8-overlap-d23ffa56');runs=['ablation-bs32-mt2-089c47c70dc2','ablation-bs32-mt2-307300a404ba']
buffer=io.BytesIO()
with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
 for run in runs:
  result=json.loads((root/run/'result.json').read_text());assert result['status']=='PASS' and len(result['ranks'])==16
  for d in (root/run).glob('rank-*'):
   records=d/'records';summaries=list(records.glob('*.summary.json'));assert len(summaries)==15
   for p in summaries:assert json.loads(p.read_text())['correctness']
  for p in (root/run).rglob('*'):
   if p.is_file():z.write(p,str(p.relative_to(root)))
 for p in (root/'inputs').rglob('*'):
  if p.is_file():z.write(p,str(p.relative_to(root)))
 z.write(root/'build-receipt.json','build-receipt.json')
 if (root/'source').exists():
  for relative in ['src/ep','tests/ep/ubx_full_intra','tools/profiling']:
   for p in (root/'source'/relative).rglob('*'):
    if p.is_file() and '__pycache__' not in p.parts:z.write(p,str(p.relative_to(root)))
sys.stdout.buffer.write(buffer.getvalue())
'''
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
 for host in ['90.90.93.83','90.90.93.86']:
  p=subprocess.run(ssh+['root@'+host,'python3 -'],input=remote.encode(),capture_output=True,timeout=240)
  if p.returncode:print(json.dumps(dict(host=host,error=p.stderr.decode(errors='replace')[-1500:])))
  assert p.returncode==0
  import io
  with zipfile.ZipFile(io.BytesIO(p.stdout)) as source:
   for name in source.namelist():z.writestr(host+'/'+name,source.read(name))
 stats=Path('D:/work/UBX攻关/UBX_Control/tilexr-ep32-rdma-group8-overlap-stats')
 # Timing and work counters retain explicit units; old generic stats labels are corrected on export.
 rows=json.loads((stats/'summary.json').read_text())
 for row in rows:
  unit='us' if row['metric'].endswith('_us') else ('bytes' if row['metric']=='rdma_bytes' else 'count')
  row['unit']=unit
  if unit!='us':
   for name in ['mean','p95','max']:row[name]=row.pop(name+'_us')
 (stats/'summary.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
 for p in stats.iterdir():
  if p.is_file():z.write(p,'statistics/'+p.name)
 z.writestr('README.txt','EP32 BS32 maxTokens2, servers83/86, profiling ON. Source tested 568c1b306aa66eee742ed6c6e633ceccd9ea4389. Control means no-control-exchange Server-peer baseline, not original control-exchange kernel. G8 uses per-target-group RDMA, then group-local relay. Each variant 5 warmup +10 measured; all32 ranks validated. Event excludes sleep and postbarrier; core spans are same-card only. Raw SDK captures, clock files, summaries, inputs, source and build provenance included; native msprof not used. Branch codex/ubx-rdma-group8. Corrected G8 SEND publishes and rings both peers before draining either peer; packet-trace mode is OFF. Summary counters explicitly labelled bytes/count.')
with zipfile.ZipFile(out) as z:files=len(z.namelist());assert any(n.endswith('.records.bin') for n in z.namelist())
print(json.dumps(dict(archive=str(out),archive_bytes=out.stat().st_size,archive_sha256=hashlib.sha256(out.read_bytes()).hexdigest(),files=files)))
