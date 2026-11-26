# Copyright (c) 2026 TileXR Project.
"""Clone immutable failed inputs and dump queues at a diagnostic SST stop point."""
from collections import Counter
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import struct
import sys
import time

ROOT=Path('/work')
ORIGINAL=ROOT/'cases/t32-inter-server-send-a2'
CASE=ROOT/'cases/t32-inter-server-send-snapshot-a1'
SUPPORT=ROOT/'source/tools/unitest/simulator/experiments/inter_server_send/sst'


def main():
    key=sys.argv[1]
    assert re.fullmatch('[0-9a-f]{16}',key)
    tools=ROOT/'tools'/key
    assert tools.is_dir()
    sys.path[:0]=[str(tools),str(SUPPORT)]
    from sim_case import run_sst,write_json
    from addressed_exports import reconstruct_die_output
    from runtime import BASES,BB_BYTES,PHYSICAL_QPS,WORLD
    previous=json.loads((ORIGINAL/'phase-result.json').read_text())
    assert previous['status']=='timed_out' and previous['candidate_commit']=='71b62db945c23268df029ecb6a4e8809a7579222'
    assert previous['inter_server_rank_send'] and previous['profiling_enabled']
    assert not CASE.exists()
    CASE.mkdir()
    for folder in ('input_bin','kernel'):
        shutil.copytree(ORIGINAL/folder,CASE/folder,copy_function=os.link)
    for name in ('v310_config','UBusSim_config'):
        shutil.copytree(ORIGINAL/name,CASE/name)
    for name in ('test_UBusSim.py','sst-environment.json','environment.snapshot.json','installed_model.py','export-layout.json'):
        shutil.copyfile(ORIGINAL/name,CASE/name)
    workload=json.loads((ORIGINAL/'workload.yml').read_text().replace(str(ORIGINAL),str(CASE)))
    plane=WORLD*PHYSICAL_QPS*BB_BYTES
    for chip in workload['workload_config'].values():
        chip['output_param']['qstate']=dict(addr=chip['sys_va_base_addr']+BASES['queue_state'],size=5*plane)
    write_json(CASE/'workload.yml',workload)
    write_json(CASE/'v310_config/workload_config.yml',workload)
    assert hashlib.sha256((CASE/'kernel/inter_server_send.o').read_bytes()).hexdigest()==previous['kernel_sha256']
    environment=json.loads((CASE/'sst-environment.json').read_text())
    record=dict(diagnostic_only=True,tools_key=key,source_case=ORIGINAL.name,stop_at_us=130,
                candidate_commit=previous['candidate_commit'],kernel_sha256=previous['kernel_sha256'],
                routes_sha256=previous['routes_sha256'])
    try:
        run_sst(CASE,environment,1200,record,130)
        layout=json.loads((CASE/'export-layout.json').read_text())
        decoder=Path(environment['decoder_library'])
        def read(row,name,address,size):
            return reconstruct_die_output([CASE/'bin'/f"david_{row['rank']}_die{die}_{name}.bin" for die in (1,2)],address,size,decoder)
        statuses=Counter()
        pending=[]
        completion={}
        missing=Counter()
        for row in layout['ranks']:
            for name in ('status','completed','qstate'):
                try:
                    region=row['exports'].get(name)
                    address,size=(BASES['queue_state'],5*plane) if name=='qstate' else (region['addr']-row['sys_va_base_addr'],region['size'])
                    raw=read(row,name,address,size)
                    if name=='status':
                        statuses.update(struct.unpack_from('<I',raw,lane*32)[0] for lane in range(16))
                    elif name=='completed': completion[row['rank']]=raw
                    else:
                        for slot in range(WORLD*PHYSICAL_QPS):
                            values=[struct.unpack_from('<I',raw,i*plane+slot*BB_BYTES)[0] for i in range(5)]
                            if values[1]!=values[2]:
                                peer,physical=divmod(slot,PHYSICAL_QPS)
                                pending.append(dict(rank=row['rank'],peer=peer,slot=physical,
                                                    count=values[0],sq_head=values[1],sq_tail=values[2],cq_head=values[3],cq_tail=values[4]))
                except (FileNotFoundError,ValueError): missing[name]+=1
        visible=Counter()
        for q in pending:
            if q['peer'] not in completion:
                visible['unknown']+=1
                continue
            at=(8+(q['rank']*4+3)*2)*512
            value=struct.unpack_from('<q',completion[q['peer']],at)[0]
            visible['present' if value==2026 else 'absent']+=1
            q['rank_completion']=value
        record.update(status='diagnostic_snapshot',lane_status_histogram=dict(statuses),
                      pending_queue_count=len(pending),pending_queues=pending,missing_exports=dict(missing),
                      rank_notification_visibility=dict(visible))
    except BaseException as error:
        record.update(status='diagnostic_failed',error_class=type(error).__name__,error=str(error)[:200])
    write_json(CASE/'diagnostic-result.json',record)
    compact={key:value for key,value in record.items() if key not in ('command','pending_queues')}
    compact['pending_queues']=record.get('pending_queues',[])[:6]
    raw=json.dumps(compact,separators=(',',':'))
    assert len(raw.encode())<=4096
    print(raw)
    return 0 if record['status']=='diagnostic_snapshot' else 1


if __name__=='__main__':
    raise SystemExit(main())
