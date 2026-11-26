# Copyright (c) 2026 TileXR Project.
"""Snapshot joint stage status at the original perf logging level using unchanged inputs."""
from collections import Counter,deque
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import time

ROOT=Path('/work')
ORIGINAL=ROOT/'cases/t32-joint-reuse-a2'
CASE=ROOT/'cases/t32-joint-reuse-state-snapshot-a1'
SUPPORT=ROOT/'source/tools/unitest/simulator/experiments/inter_server_send/sst'
ROLES={'local_send':(0,8,4),'gateway_reduce':(24*4,8,4),
       'local_return':(512,8,32),'inter_server_send':(1024,16,32),'final_reduce':(1536,32,32)}


def main():
    key=sys.argv[1]
    assert re.fullmatch('[0-9a-f]{16}',key)
    sys.path[:0]=[str(ROOT/'tools'/key),str(SUPPORT)]
    from sim_case import write_json
    from addressed_exports import reconstruct_die_output
    from runtime import BASES,BB_BYTES,PHYSICAL_QPS,WORLD
    previous=json.loads((ORIGINAL/'phase-result.json').read_text())
    assert previous['status'] in ('timed_out','interrupted')
    assert previous['candidate_commit']=='b61cb530b0d25d1e9ad18748ee7aeb7a7962740e'
    assert previous['blocks']==32 and previous['final_pipeline_mode']==2
    CASE.mkdir()
    for folder in ('input_bin','kernel'):
        shutil.copytree(ORIGINAL/folder,CASE/folder,copy_function=os.link)
    for folder in ('v310_config','UBusSim_config'):
        shutil.copytree(ORIGINAL/folder,CASE/folder)
    for name in ('test_UBusSim.py','sst-environment.json','environment.snapshot.json','installed_model.py','export-layout.json'):
        shutil.copyfile(ORIGINAL/name,CASE/name)
    assert hashlib.sha256((CASE/'kernel/inter_server_send.o').read_bytes()).hexdigest()==previous['kernel_sha256']
    workload=json.loads((ORIGINAL/'workload.yml').read_text().replace(str(ORIGINAL),str(CASE)))
    plane=WORLD*PHYSICAL_QPS*BB_BYTES
    for chip in workload['workload_config'].values():
        chip['output_param']['qstate']=dict(addr=chip['sys_va_base_addr']+BASES['queue_state'],size=5*plane)
    write_json(CASE/'workload.yml',workload)
    write_json(CASE/'v310_config/workload_config.yml',workload)
    config=CASE/'v310_config/davidV100_simulator_config.yml'
    environment=json.loads((CASE/'sst-environment.json').read_text())
    record=dict(diagnostic_only=True,tools_key=key,source_case=ORIGINAL.name,stop_at_us=155,
                candidate_commit=previous['candidate_commit'],kernel_sha256=previous['kernel_sha256'],
                routes_sha256=previous['routes_sha256'],configuration_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
                dump_chips=[],wall_limit_seconds=1200,dump_limit_bytes=4*1024**3)
    command=['bash','-c','set -e; source "$1"; shift; exec "$@"','bash',environment['env_script'],
             environment['sst_bin'],'-n','8','--stop-at=155us',str(CASE/'test_UBusSim.py')]
    start=time.monotonic()
    record['started_unix']=time.time()
    with (CASE/'sim.log').open('wb') as log:
        child=subprocess.Popen(command,cwd=CASE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        record['pid']=child.pid
        write_json(CASE/'diagnostic.json',record)
        while child.poll() is None:
            total=sum(p.stat().st_size for p in (CASE/'dumps').rglob('*') if p.is_file())
            if time.monotonic()-start>=1200 or total>=record['dump_limit_bytes']:
                record['stop_reason']='wall_limit' if time.monotonic()-start>=1200 else 'dump_limit'
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL)
                    child.wait(timeout=10)
                break
            time.sleep(2)
    record.update(sst_wall_seconds=round(time.monotonic()-start,3),returncode=child.returncode,finished_unix=time.time())
    write_json(CASE/'diagnostic.json',record)
    if child.returncode or 'stop_reason' in record:
        record['status']='diagnostic_failed'
    else:
        layout=json.loads((CASE/'export-layout.json').read_text())
        decoder=Path(environment['decoder_library'])
        roles={name:Counter() for name in ROLES}
        matches=Counter();missing=Counter();pending=[];ready=Counter()
        for row in layout['ranks']:
            rank=row['rank']
            for field,region in row['exports'].items():
                if field=='profile':continue
                try:
                    raw=reconstruct_die_output([CASE/'bin'/f'david_{rank}_die{die}_{field}.bin' for die in (1,2)],
                                               region['addr']-row['sys_va_base_addr'],region['size'],decoder)
                except (FileNotFoundError,ValueError):
                    missing[field]+=1
                    continue
                if field=='status':
                    for name,(base,count,stride) in ROLES.items():
                        roles[name].update(struct.unpack_from('<I',raw,base+lane*stride)[0] for lane in range(count))
                else:
                    golden=ORIGINAL/'fixture-data'/f'rank{rank:02}'/f'golden-{field}.bin'
                    matches[field+('_match' if raw==golden.read_bytes() else '_different')]+=1
                if field=='completed':
                    for reducer in range(8):
                        magic,epoch=struct.unpack_from('<qq',raw,reducer*512)
                        ready[f'gateway_ready_{magic}_{epoch}']+=1
                    for gateway in range(64):
                        if gateway//8!=rank//8:
                            value=struct.unpack_from('<q',raw,(8+(gateway*4+3)*2)*512)[0]
                            ready['remote_so_present' if value==2026 else 'remote_so_missing']+=1
            try:
                state=reconstruct_die_output([CASE/'bin'/f'david_{rank}_die{die}_qstate.bin' for die in (1,2)],
                                             BASES['queue_state'],5*plane,decoder)
                for slot in range(WORLD*PHYSICAL_QPS):
                    values=[struct.unpack_from('<I',state,i*plane+slot*BB_BYTES)[0] for i in range(5)]
                    if values[1]!=values[2]:
                        peer,physical=divmod(slot,PHYSICAL_QPS)
                        pending.append(dict(rank=rank,peer=peer,slot=physical,count=values[0],sq_head=values[1],sq_tail=values[2],cq_tail=values[4]))
            except (FileNotFoundError,ValueError):missing['qstate']+=1
        record.update(status='diagnostic_snapshot',role_status={name:dict(value) for name,value in roles.items()},
                      field_comparison=dict(matches),ready=dict(ready),missing_exports=dict(missing),
                      pending_queue_count=len(pending),pending_queues=pending)
        trace=[]
        for path in sorted((CASE/'dumps').rglob('*sudetail.dump')):
            if path.is_file():
                with path.open(errors='replace') as f:tail=list(deque(f,maxlen=8))
                trace.append(dict(file=str(path.relative_to(CASE)),bytes=path.stat().st_size,tail=[line[:450].strip() for line in tail]))
        record['scalar_trace']=trace
    write_json(CASE/'diagnostic-result.json',record)
    compact={name:value for name,value in record.items() if name not in ('pending_queues','scalar_trace')}
    compact['pending_queues']=record.get('pending_queues',[])[:4]
    compact['scalar_detail_files']=len(record.get('scalar_trace',[]))
    raw=json.dumps(compact,separators=(',',':'))
    assert len(raw.encode())<=4096
    print(raw)
    return 0 if record['status']=='diagnostic_snapshot' else 1


if __name__=='__main__':
    raise SystemExit(main())
