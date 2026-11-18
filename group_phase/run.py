"""串行运行有限匹配诊断；保留每例原始结果并回传聚合阶段耗时。"""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

args = arguments(); revision, cases = args['revision'], args['cases']
assert re.fullmatch('[0-9a-f]{40}', revision) and 1 <= len(cases) <= 3
for case in cases:
    assert set(case) == {'mode', 'size', 'target', 'label'}
    assert case['mode'] in ('sync', 'clock') and case['size'] in ('small', '512k', '4m')
    assert type(case['target']) is int and 0 <= case['target'] < 1 << 63
    assert re.fullmatch('[a-z0-9-]{1,60}', case['label'])
root = '/opt/sim/alltoall-group-phase-'+revision[:8]+'-20261118'
source = Path(__file__).with_name('case_remote.py').read_text()
remote = '''import json,pathlib,subprocess,sys
root=pathlib.Path(ROOT_VALUE)
assert json.loads((root/'build-summary.json').read_text())['runtime_commit']==REV_VALUE
script=root/'phase-case.py';source=SOURCE_VALUE
if script.exists():assert script.read_text()==source,'preserve runner identity'
else:script.write_text(source)
for case in CASES_VALUE:
 p=subprocess.run([sys.executable,str(script),str(root),json.dumps(case)],capture_output=True,text=True,timeout=960)
 assert p.returncode==0,json.dumps(dict(runner_exit=p.returncode,error_tail=p.stderr.splitlines()[-4:]))
 row=json.loads(p.stdout);print(json.dumps(row),flush=True)
 assert row['remaining_processes']==[]
'''.replace('ROOT_VALUE', repr(root)).replace('REV_VALUE', repr(revision))
remote = remote.replace('SOURCE_VALUE', repr(source)).replace('CASES_VALUE', repr(cases))
in_container(remote, 980*len(cases))
