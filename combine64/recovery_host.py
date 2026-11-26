# Copyright (c) 2026 TileXR Project.
"""Stop only the verified stalled runner, or snapshot its failed send peer."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

SESSION='59af0a93-b62f-4211-927e-a44f047667cf'
BASE=Path('/mnt/share/tilexr-sim-jobs')/SESSION

STOP=r'''import json,os,signal,time
from pathlib import Path
case=Path('/work/cases/t32-joint-reuse-a1')
terminal=case/'phase-result.json'
if terminal.is_file():
 result=json.loads(terminal.read_text())
else:
 run=json.loads((case/'run.json').read_text())
 assert run['status']=='running' and run['candidate_commit']=='2dfd2b47394d013164f9270b445aa0bd39321755'
 assert (run['stage'],run['tokens'],run['attempt'])==('joint-reuse',32,1)
 assert time.time()-(case/'sim.log').stat().st_mtime>900
 process=Path('/proc')/str(run['pid'])
 assert (process/'cwd').resolve()==case
 parent=int((process/'stat').read_text().rsplit(')',1)[1].split()[1])
 argv=(Path('/proc')/str(parent)/'cmdline').read_bytes().rstrip(bytes([0])).split(bytes([0]))
 assert len(argv)==3 and argv[1].endswith(b'/sim_case.py')
 config=json.loads(argv[2])
 assert (config['action'],config['tokens'],config['attempt'],config['revision'])==('joint-reuse',32,1,run['candidate_commit'])
 os.kill(parent,signal.SIGTERM)
 deadline=time.monotonic()+35
 while not terminal.is_file() and time.monotonic()<deadline: time.sleep(1)
 assert terminal.is_file(),'terminal_record_pending'
 result=json.loads(terminal.read_text())
print(json.dumps({key:result[key] for key in ('status','sst_wall_seconds','runner_wall_seconds','returncode','stage','tokens','attempt') if key in result},separators=(',',':')))
'''


def main(request):
    action=request['action']
    assert action in ('stop-joint','snapshot-send','read-cq','snapshot-joint','read-joint-trace','read-joint-waits','read-joint-mte','snapshot-joint-state','snapshot-joint-early','read-joint-early')
    candidate='b61cb53' if action in ('snapshot-joint','read-joint-trace','read-joint-waits','read-joint-mte','snapshot-joint-state','snapshot-joint-early','read-joint-early') else ('2dfd2b4' if action=='stop-joint' else '71b62db')
    root=BASE/f'run-{candidate}-a1'
    identity=json.loads((root/'identity.json').read_text())
    assert identity['revision'].startswith(candidate)
    cid=identity['container_id']
    info=json.loads(subprocess.check_output(['docker','inspect',cid],timeout=10))[0]
    assert info['Id']==cid and info['Config']['Labels']['crosszone.session']==SESSION
    if action=='snapshot-joint':
        terminal=root/'cases/t32-joint-reuse-a2/phase-result.json'
        deadline=time.monotonic()+300
        while not terminal.is_file() and time.monotonic()<deadline:time.sleep(2)
        assert terminal.is_file(),'prior_joint_still_running'
        assert json.loads(terminal.read_text())['status'] in ('timed_out','interrupted')
    if action=='snapshot-joint-state':
        previous=json.loads((root/'cases/t32-joint-reuse-snapshot-a1/diagnostic-result.json').read_text())
        assert previous['status']=='diagnostic_failed' and previous.get('stop_reason')=='wall_limit'
    if action=='snapshot-joint-early':
        previous=json.loads((root/'cases/t32-joint-reuse-state-snapshot-a1/diagnostic-result.json').read_text())
        assert previous['status']=='diagnostic_failed' and previous.get('stop_reason')=='wall_limit'
    if action=='stop-joint':
        argv=['docker','exec',cid,'python3','-c',STOP]
        timeout=50
    else:
        content=request['snapshot_script']
        target=root/'diagnostic-tools'/hashlib.sha256(content.encode()).hexdigest()[:16]/'snapshot_case.py'
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists(): assert target.read_text()==content
        else: target.write_text(content)
        argv=['docker','exec',cid,'timeout','--kill-after=20','1400','python3',str(Path('/work')/target.relative_to(root)),request['tool_key']]
        timeout=1440
        if action=='snapshot-joint':
            argv[argv.index('1400')]='1750'
            timeout=1800
        if action=='snapshot-joint-early':
            argv[argv.index('1400')]='1100'
            timeout=1140
        if action in ('read-cq','read-joint-trace','read-joint-waits','read-joint-mte','read-joint-early'):
            argv=['docker','exec',cid,'python3',str(Path('/work')/target.relative_to(root))]
            timeout=50
    stem=action+'-'+hashlib.sha256((STOP if action=='stop-joint' else request['snapshot_script']).encode()).hexdigest()[:12]
    with (root/(stem+'.stdout')).open('wb') as out,(root/(stem+'.stderr')).open('wb') as err:
        p=subprocess.run(argv,stdout=out,stderr=err,timeout=timeout)
    raw=(root/(stem+'.stdout')).read_bytes()
    if p.returncode==0:
        assert len(raw)<=4096
        print(json.dumps(json.loads(raw),separators=(',',':')))
    else:
        errors=(root/(stem+'.stderr')).read_text(errors='replace').splitlines()
        classes=[name for name in ('AssertionError','ValueError','TimeoutExpired','FileNotFoundError') if any(name in line for line in errors)]
        report=dict(action=action,exit_code=p.returncode,error_classes=classes)
        if raw.strip():
            try: report['summary']=json.loads(raw)
            except ValueError: pass
        print(json.dumps(report,separators=(',',':')))
    return p.returncode
