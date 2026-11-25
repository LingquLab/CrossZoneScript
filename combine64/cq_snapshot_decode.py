"""Decode available CQ records without inventing bytes absent from sparse dumps."""
import json,struct
from collections import Counter
from pathlib import Path
import exports
from cq_snapshot import cq_address,PLANE,CASE

def sparse(path,address,size):
    blocks={};length=0
    with path.open('rb') as f:
        while True:
            raw=f.read(520)
            if not raw:break
            assert len(raw)==520
            base=int.from_bytes(raw[:8],'little')
            if base<address+size and base+512>address:
                if base in blocks:assert blocks[base]==raw[8:]
                blocks[base]=raw[8:]
            length+=1
    return blocks,length
def field(blocks,address):
    base=address//512*512
    if base not in blocks:return None
    return struct.unpack_from('<II',blocks[base],address-base)
def main():
    diag=json.loads((CASE/'diagnostic.json').read_text())
    assert diag['sst_exit']==0 and diag['stop_at']=='40us' and not diag.get('wall_timeout')
    env=json.loads((CASE/'sst-environment.json').read_text());rows=[]
    for rank in range(64):
        paths=[CASE/'bin'/('david_%d_die%d_qstate.bin'%(rank,die)) for die in (1,2)]
        q=exports.reconstruct_die_output(paths,0x1b00000000,5*PLANE,env['decoder_library'])
        for g in range(8):
            peer=rank//8*8+g;physical=g%2*20+g//2;flat=peer*40+physical
            counts={k:struct.unpack_from('<I',q,i*PLANE+flat*64)[0] for i,k in enumerate(('count','sqhead','sqtail','cqhead','cqtail'))}
            if not any(counts.values()):continue
            local=cq_address(rank,g)
            copies=[sparse(CASE/'bin'/('david_%d_die%d_cq%d.bin'%(rank,die,g)),local,1024) for die in (1,2)]
            entries=[]
            for i in range(16):
                address=local+i*64;die=((address>>9)^(address>>16)^(address>>23))&1
                values=[field(copy[0],address) for copy in copies];v=values[die]
                entries.append([i,None if v is None else (v[0]>>2)&1,None if v is None else v[0]>>24,
                    None if v is None else (v[0]>>16)&255,None if v is None else v[1]&65535,
                    *[None if x is None else (x[0]>>2)&1 for x in values]])
            rows.append(dict(rank=rank,gateway=g,cq_local=local,**counts,entries=entries,raw_dump_records=[c[1] for c in copies]))
    pending=[row for row in rows if row['sqhead']!=row['sqtail']]
    report=dict(diagnostic_only=True,operator_qualified=False,source_case=str(CASE),sst_wall_seconds=diag['sst_wall_seconds'],
        stop_at=diag['stop_at'],active_queues=len(rows),pending_count=len(pending),
        pending_sqtail_histogram=dict(Counter(x['sqtail'] for x in pending)),
        pending=pending[:24],completed_over8_samples=[x for x in rows if x['sqhead']>8 and x['sqhead']==x['sqtail']][:3],
        entry_columns=['index','owner_verified_die','status','substatus','entry_index','owner_die1','owner_die2'],
        missing_data_policy='null denotes absent dump bytes; absent is never interpreted as zero or an unwritten CQE')
    (CASE/'all-queues-sparse.json').write_text(json.dumps(rows,indent=2)+'\n')
    (CASE/'cq-sparse-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,separators=(',',':')),flush=True);return 0
if __name__=='__main__':raise SystemExit(main())
