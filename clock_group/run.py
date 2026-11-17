"""顺序运行有限的新用例；每例独立输出目录，失败后不覆盖或回放。"""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

args = arguments(); revision = args['revision']; cases = args['cases']
assert re.fullmatch('[0-9a-f]{40}', revision) and 1 <= len(cases) <= 3
for case in cases:
    assert set(case) == {'size', 'variant', 'label', 'target', 'frequency'}
    assert case['size'] in ('small', '512k', '4m') and case['variant'] in ('clock', 'control')
    assert re.fullmatch('[a-z0-9-]{1,60}', case['label'])
    assert type(case['target']) is int and 0 <= case['target'] < (1 << 63)
    assert case['frequency'] in (50000000, 1000000000)
root = '/opt/sim/alltoall-clock-group-' + revision[:8] + '-20261117'
source = Path(__file__).with_name('case_remote.py').read_text()
remote = '''import json,pathlib,subprocess,sys
root=pathlib.Path(ROOT_VALUE)
summary=json.loads((root/'build-summary.json').read_text())
assert summary['runtime_commit']==REV_VALUE
script=root/'case-clock.py';source=SOURCE_VALUE
if script.exists():assert script.read_text()==source,'preserve runner identity'
else:script.write_text(source)
rows=[]
for case in CASES_VALUE:
 p=subprocess.run([sys.executable,str(script),str(root),json.dumps(case)],capture_output=True,text=True,timeout=960)
 assert p.returncode==0,json.dumps(dict(runner_exit=p.returncode,error_tail=p.stderr.splitlines()[-4:]))
 row=json.loads(p.stdout);rows.append(row)
 print(json.dumps(row),flush=True)
 assert not row.get('remaining_processes'),'owned processes remain'
print(json.dumps(dict(status='batch_completed',cases=len(rows),validated=sum(r['validated'] for r in rows))))
'''.replace('ROOT_VALUE', repr(root)).replace('REV_VALUE', repr(revision))
remote = remote.replace('SOURCE_VALUE', repr(source)).replace('CASES_VALUE', repr(cases))
in_container(remote, 980*len(cases))
