"""Host30上串行运行独立调度候选；不因soft miss丢弃已完成样本。"""
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments,in_container

args=arguments();revision,cases=args['revision'],args['cases']
assert re.fullmatch('[0-9a-f]{40}',revision) and 1<=len(cases)<=8
for case in cases:
    assert {'mode','trace','peer_bytes','label'}<=set(case)<= {
        'mode','trace','peer_bytes','label','width','targets','plan_id','max_wait_cycles','delay_cycles',
        'model_reference_error_cycles','model_rate_q32'}
    assert case['mode'] in ('original','sync','cross-first','defer-output','no-gate','local-all','local-cross','affine-all')
    assert case['trace'] in ('perf','profile') and case['peer_bytes'] in (512,8192,65536)
    assert case['mode']!='original' or case['trace']=='perf'
    assert re.fullmatch('[a-z0-9-]{1,60}',case['label'])
root='/opt/sim/alltoall-group-schedule-'+revision[:8]+'-20261118'
script=Path(__file__).with_name('case_remote.py').read_text()
remote='''import hashlib,json,pathlib,subprocess,sys
root=pathlib.Path(ROOT_VALUE)
assert json.loads((root/'build-summary.json').read_text())['runtime_commit']==REV_VALUE
source=SOURCE_VALUE
script=root/('case-runner-'+hashlib.sha256(source.encode()).hexdigest()[:12]+'.py')
if script.exists():assert script.read_text()==source
else:script.write_text(source)
for case in CASES_VALUE:
 p=subprocess.run([sys.executable,str(script),str(root),json.dumps(case)],capture_output=True,text=True,timeout=960)
 assert p.returncode==0,json.dumps(dict(runner_exit=p.returncode,error_tail=p.stderr.splitlines()[-4:]))
 row=json.loads(p.stdout);print(json.dumps(row),flush=True)
 assert row['validated'] and row['remaining_processes']==[], 'stop dependent cases on invalid payload or cleanup'
'''.replace('ROOT_VALUE',repr(root)).replace('REV_VALUE',repr(revision))
remote=remote.replace('SOURCE_VALUE',repr(script)).replace('CASES_VALUE',repr(cases))
in_container(remote,980*len(cases))
