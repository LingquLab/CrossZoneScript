"""只读提取已验收64P阶段均值和每rank完成通信窗口，不重跑仿真。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import in_container

script = '''import json,sys
from pathlib import Path
root=Path('/opt/sim/alltoall-group-phase-b68b6f3c-20261118')
sys.path.insert(0,str(root/'source/tools/unitest'))
from controller import direct_group_profile as profile
rows=[]
for mode in ('sync','clock'):
 for size in ('small','512k','4m'):
  output=root/'cases'/(mode+'-'+size+'-r1-20261118')
  report=json.loads((output/'result.json').read_text())
  summary=json.loads((output/'summary.json').read_text())
  assert report['status']=='passed' and summary['validated']
  ranks=report['operator_result']['ranks'];assert len(ranks)==64
  same=[];cross=[];combined=[]
  for rank in ranks:
   records=rank['phase_records'];assert len(records)==64
   a=[r['stamps'] for r in records if r['same_group']]
   b=[r['stamps'] for r in records if not r['same_group']]
   first=max(s[3] for s in a)-min(s[2] for s in a)
   second=max(s[6] for s in b)-min(s[5] for s in b)
   same.append(first);cross.append(second);combined.append(first+second)
  phase=report['phase_profile']
  rows.append(dict(mode=mode,size=size,bytes_per_peer=summary['bytes_per_peer'],
   simulated_us=summary['simulated_us'],groups=phase['groups'],rank_metrics=phase['rank_metrics'],
   communication_windows=dict(same=profile.stats(same),cross=profile.stats(cross),sum=profile.stats(combined))))
print(json.dumps(dict(read_only=True,simulation_launched=False,units='raw_SYS_CNT_cycles',records=rows)))
'''
in_container(script,120)
