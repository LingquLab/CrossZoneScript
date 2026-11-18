"""只读列出本轮所有已创建case，包括未完成和失败项，避免只统计有利样本。"""
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments,in_container
args=arguments();revision=args['revision']
assert re.fullmatch('[0-9a-f]{40}',revision)
root='/opt/sim/alltoall-group-schedule-'+revision[:8]+'-20261118'
remote='''import json,pathlib
root=pathlib.Path(ROOT_VALUE)
rows=[]
for case in sorted((root/'cases').iterdir()):
 if not case.is_dir():continue
 path=case/'summary.json'
 if not path.exists():
  rows.append(dict(label=case.name,complete=False,payload_valid=False));continue
 row=json.loads(path.read_text());phase=row.get('schedule_profile') or {}
 rows.append(dict(label=case.name,complete=True,mode=row['mode'],trace=row['trace'],
   peer_bytes=row['bytes_per_peer'],width=phase.get('width'),targets=phase.get('targets'),
   payload_valid=row['validated'],simulated_us=row['simulated_us'],
   scheduling_on_time=phase.get('scheduling_on_time'),misses=phase.get('scheduling_misses'),
   scheduling_errors=phase.get('scheduling_errors'),returncode=row['returncode'],
   cleanup=row['remaining_processes']==[],error=row.get('error')))
assert len(rows)<=100, 'split inventory to bound direct export'
print(json.dumps(dict(read_only=True,simulation_launched=False,runtime_commit=REV_VALUE,
  case_count=len(rows),payload_passed=sum(r['payload_valid'] for r in rows),records=rows)))
'''.replace('ROOT_VALUE',repr(root)).replace('REV_VALUE',repr(revision))
in_container(remote,120)
