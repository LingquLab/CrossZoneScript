# Copyright (c) 2026 TileXR Project.
"""Read producer progress and existing CQ log evidence from the completed early snapshot."""
from collections import Counter
import json
from pathlib import Path
import re
import struct
import sys

ROOT=Path('/work')
CASE=ROOT/'cases/t32-joint-reuse-state-snapshot-a2'


def main():
    record=json.loads((CASE/'diagnostic-result.json').read_text())
    assert record['diagnostic_only'] and record['status']=='diagnostic_snapshot' and record['stop_at_us']==60
    assert record['candidate_commit']=='b61cb530b0d25d1e9ad18748ee7aeb7a7962740e'
    sys.path.insert(0,str(ROOT/'tools'/record['tools_key']))
    from addressed_exports import reconstruct_die_output
    layout=json.loads((CASE/'export-layout.json').read_text())
    decoder=Path(json.loads((CASE/'sst-environment.json').read_text())['decoder_library'])
    role_counts=Counter();by_rank=Counter();ready_epochs=Counter();samples=[]
    for q in record['pending_queues']:
        role='local_gather' if q['slot']%20>=16 else ('local_return' if q['rank']//8==q['peer']//8 else 'inter_server_send')
        role_counts[role]+=1
        by_rank[q['rank']]+=1
    for row in layout['ranks']:
        rank=row['rank']
        values={}
        for name in ('status','completed','local_ready'):
            region=row['exports'][name]
            values[name]=reconstruct_die_output([CASE/'bin'/f'david_{rank}_die{die}_{name}.bin' for die in (1,2)],
                                               region['addr']-row['sys_va_base_addr'],region['size'],decoder)
        for reducer in range(8):
            magic,epoch=struct.unpack_from('<qq',values['completed'],reducer*512)
            status=struct.unpack_from('<I',values['status'],(24+reducer)*4)[0]
            if magic!=2026 or epoch!=4 or status!=0:
                samples.append(dict(rank=rank,reducer=reducer,magic=magic,epoch=epoch,status=status))
        for peer in range(8):
            flag=struct.unpack_from('<q',values['local_ready'],peer*512)[0]
            ready_epochs['local_return_published' if flag==2026 else 'local_return_unpublished']+=1
    cq_lines=[];counts=Counter();last_cycle=None
    with (CASE/'sim.log').open(errors='replace') as f:
        for line in f:
            if 'CQE' not in line:continue
            for name in ('WrCQEResp','WrCQEReq'):
                if name in line:counts[name]+=1
            match=re.search(r'Sim: ([0-9]+)[(]cycle[)]',line)
            if match:last_cycle=int(match[1])
            if 'WrCQEResp' in line:
                cq_lines.append(line.strip()[:400])
                if len(cq_lines)>2:cq_lines.pop(0)
    result=dict(status='existing_joint_snapshot_read',diagnostic_only=True,stop_at_us=60,
                pending_queue_roles=dict(role_counts),pending_by_rank=dict(by_rank),
                producer_incomplete_samples=samples,local_ready=dict(ready_epochs),cq_event_counts=dict(counts),
                last_cq_cycle=last_cycle,cq_log_samples=cq_lines,
                cq_export_files=sum(1 for p in (CASE/'bin').glob('*cq_peer*') if p.is_file()))
    (CASE/'producer-read-result.json').write_text(json.dumps(result,indent=2)+'\n')
    compact=dict(result)
    compact['producer_incomplete_samples']=samples[:5]
    compact['pending_by_rank']={str(k):v for k,v in by_rank.most_common(8)}
    raw=json.dumps(compact,separators=(',',':'))
    assert len(raw.encode())<=4096
    print(raw)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
