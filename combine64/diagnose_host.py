# Copyright (c) 2026 TileXR Project.
"""Read this session's case progress and process identity without altering execution."""
import json
import os
from pathlib import Path
import re
import subprocess
import time

SESSION='59af0a93-b62f-4211-927e-a44f047667cf'
ROOT=Path('/mnt/share/tilexr-sim-jobs')/SESSION
CASES=(('b61cb53','t32-joint-reuse-a2'),)


def sample(candidate,name):
    root=ROOT/f'run-{candidate}-a1'
    identity=json.loads((root/'identity.json').read_text())
    cid=identity['container_id']
    info=json.loads(subprocess.check_output(['docker','inspect',cid],timeout=10))[0]
    assert info['Id']==cid and info['Config']['Labels']['crosszone.session']==SESSION
    case=root/'cases'/name
    result=dict(candidate=candidate,case=name,container_running=info['State']['Running'])
    progress=root/'progress.json'
    if progress.is_file(): result['progress']=json.loads(progress.read_text())
    for filename in ('run.json','phase-result.json'):
        path=case/filename
        if path.is_file():
            data=json.loads(path.read_text())
            fields=('status','pid','started_unix','sst_wall_seconds','returncode','error','platform_simulated_us','checked_ranks')
            result[filename]={k:data[k] for k in fields if k in data}
    run=result.get('run.json',{})
    if run.get('status')=='running' and type(run.get('pid')) is int:
        pid=run['pid']
        check=subprocess.run(['docker','exec',cid,'cat',f'/proc/{pid}/stat'],capture_output=True,timeout=10)
        result['process_exists']=check.returncode==0
        if check.returncode==0:
            stat=check.stdout.decode().rsplit(')',1)[1].split()
            result.update(process_state=stat[0],cpu_ticks=int(stat[11])+int(stat[12]))
        result['elapsed_seconds']=round(time.time()-run['started_unix'],3)
    log=case/'sim.log'
    if log.is_file():
        stat=log.stat()
        with log.open('rb') as stream:
            stream.seek(max(0,stat.st_size-524288))
            tail=stream.read().decode(errors='replace')
        cycles=re.findall(r'Sim: ([0-9]+)[(]cycle[)]',tail)
        cq=re.findall(r'Sim: ([0-9]+)[(]cycle[)][^\n]*WrCQEResp',tail)
        completion=re.findall(r'Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)',tail)
        result.update(log_bytes=stat.st_size,log_age_seconds=round(time.time()-stat.st_mtime,3),
                      last_sim_cycle=int(cycles[-1]) if cycles else None,
                      last_cq_cycle=int(cq[-1]) if cq else None,
                      completion=completion[-1] if completion else None)
        result['error_classes']=[word for word in ('fatal','segmentation fault','assertion','panic') if word in tail.lower()]
    for folder in ('bin','stat_trace','actual'):
        path=case/folder
        sizes=[p.stat().st_size for p in path.glob('*') if p.is_file()] if path.exists() else []
        result[folder]=dict(files=len(sizes),bytes=sum(sizes))
    return result


def main():
    first=[sample(*case) for case in CASES]
    time.sleep(12)
    second=[sample(*case) for case in CASES]
    report=dict(first=first,second=second)
    raw=json.dumps(report,separators=(',',':'))
    assert len(raw.encode())<=4096
    print(raw)
    return 0
