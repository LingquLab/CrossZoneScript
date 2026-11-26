# Copyright (c) 2026 TileXR Project.
"""Read only covered owner-die CQE ranges from an existing diagnostic snapshot."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT=Path('/work')
CASE=ROOT/'cases/t32-inter-server-send-snapshot-a1'
SUPPORT=ROOT/'source/tools/unitest/simulator/experiments/inter_server_send/sst'


def main():
    record=json.loads((CASE/'diagnostic-result.json').read_text())
    assert record['status']=='diagnostic_snapshot' and record['diagnostic_only']
    sys.path[:0]=[str(ROOT/'tools'/record['tools_key']),str(SUPPORT)]
    from addressed_exports import read_addressed,V310_DECODER_SHA256
    from runtime import physical_queue_addresses,GM_BYTES,DEPTH
    env=json.loads((CASE/'sst-environment.json').read_text())
    assert hashlib.sha256(Path(env['decoder_library']).read_bytes()).hexdigest()==V310_DECODER_SHA256
    stats=Counter()
    rows=[]
    first={}
    for queue in record['pending_queues']:
        group=(queue['rank'],queue['slot']%20)
        order=(queue['slot']//20,queue['peer'])
        first[group]=min(first.get(group,order),order)
    for queue in record['pending_queues']:
        q=dict(queue)
        is_first=(q['slot']//20,q['peer'])==first[q['rank'],q['slot']%20]
        q['first_pending_of_owner']=is_first
        stats['lane'+str(q['slot']//20)+'_pending']+=1
        if is_first: stats['first_pending_groups']+=1
        address=physical_queue_addresses(q['rank'],q['peer'],q['slot'])['cq']-q['rank']*GM_BYTES+(q['cq_tail']%DEPTH)*64
        die=((address>>9)^(address>>16)^(address>>23))&1
        name=f"cq_peer{q['peer']}_slot{q['slot']}"
        path=CASE/'bin'/f"david_{q['rank']}_die{die+1}_{name}.bin"
        q['cq_dump_bytes']=path.stat().st_size if path.is_file() else None
        try:
            raw=read_addressed(path,address,64)
        except (FileNotFoundError,ValueError) as error:
            stats['missing_next_cqe']+=1
            q['cq_read_error']=type(error).__name__
        else:
            flags,entry=struct.unpack_from('<2I',raw)
            owner=(flags>>2)&1
            expected_owner=1-((q['cq_tail']//DEPTH)&1)
            q.update(next_owner=owner,expected_owner=expected_owner,next_status=(flags>>24)&255,
                     next_substatus=(flags>>16)&255,next_entry=entry&65535)
            stats['valid_next_owner' if owner==expected_owner else 'not_ready_next_owner']+=1
            if is_first: stats['first_pending_valid_owner' if owner==expected_owner else 'first_pending_not_ready_owner']+=1
            stats['matching_next_entry' if (entry&65535)==q['sq_tail']%DEPTH else 'different_next_entry']+=1
            if q['next_status'] or q['next_substatus']: stats['cqe_error_status']+=1
        rows.append(q)
    full=dict(status='existing_snapshot_cq_read',queue_count=len(rows),histogram=dict(stats),queues=rows)
    (CASE/'cq-read-result.json').write_text(json.dumps(full,indent=2)+'\n')
    compact=dict(status=full['status'],queue_count=len(rows),histogram=dict(stats),samples=[q for q in rows if q['first_pending_of_owner']][:6])
    raw=json.dumps(compact,separators=(',',':'))
    assert len(raw.encode())<=4096
    print(raw)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
