"""Execute a bounded serial regression batch with persistent per-case evidence.
顺序运行有限批次，逐项保留结果，拒绝覆盖已有用例。
"""
import json
from pathlib import Path
import sys,traceback
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import arguments,in_container,ROOT,REV
args=arguments();cases=args['cases'];label=args['batch']
assert label.replace('-','').isalnum() and 1<=len(cases)<=9
for c in cases:
    assert len(c)==4 and type(c[0]) is int and c[0] in (8,16,32,64)
    assert c[1] in ('small','512k','4m') and c[2] in ('memory','group','multi-b1','multi-b2','multi-b4','multi-b8','multi-b16','multi-b32')
    assert c[3] in ('ordered','data-as-flag','sentinel','two-group')
remote='''import hashlib,json,pathlib,subprocess,sys
root=pathlib.Path(ROOT_VALUE)
summary=json.loads((root/'build-summary.json').read_text());assert summary['runtime_commit']==REV_VALUE
script=root/'case-regression.py'
source=SOURCE_VALUE
if script.exists():assert script.read_text()==source,'case runner changed'
else:script.write_text(source)
log=root/('batch-'+LABEL_VALUE+'.json');assert not log.exists(),'preserve batch evidence'
rows=[]
for case in CASES_VALUE:
 p=subprocess.run([sys.executable,str(script),str(root)]+[str(x) for x in case],capture_output=True,text=True,timeout=900)
 if p.returncode:
  row=dict(case=case,runner_exit=p.returncode,error_tail=p.stderr.splitlines()[-4:])
  rows.append(row);log.write_text(json.dumps(rows,indent=2));print(json.dumps(dict(status='batch_stopped',records=rows)));raise SystemExit(1)
 row=json.loads(p.stdout.strip());rows.append(row)
 temp=log.with_suffix('.tmp');temp.write_text(json.dumps(rows,indent=2));temp.replace(log)
 if row['remaining_processes']:
  print(json.dumps(dict(status='owned_process_remains',records=rows)));raise SystemExit(1)
print(json.dumps(dict(status='batch_completed',passed=sum(bool(r.get('validated')) for r in rows),case_count=len(rows),records=rows)))
'''.replace('ROOT_VALUE',repr(ROOT)).replace('REV_VALUE',repr(REV)).replace('SOURCE_VALUE',repr(Path(__file__).with_name('case_remote.py').read_text())).replace('LABEL_VALUE',repr(label)).replace('CASES_VALUE',repr(cases))
try:in_container(remote,900*len(cases)+30)
except Exception as exc:print(json.dumps(dict(status='execution_failed',error_type=type(exc).__name__,error=str(exc)[:300])))
