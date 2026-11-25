"""Check all nine output regions, including each active AIV status."""
import argparse
from collections import Counter
import json
from pathlib import Path
import struct

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['check'])
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--actual', type=Path, required=True)
args = parser.parse_args()
fields = ('gather','gather_ready','partial','ready','local_ready','returned','completed','output','status')
roles = dict(gather=(0,8,4),reduce=(32,8,4),local_return=(256,8,32),
             cross_return=(512,16,32),final=(1024,16,32))
status = {role:Counter() for role in roles}
counts = Counter()
errors = Counter()
first = {}
for rank in range(64):
    name=f'rank{rank:02d}'
    for field in fields:
        raw=(args.actual/name/(field+'.bin')).read_bytes()
        expected=(args.output/name/('golden-'+field+'.bin')).read_bytes()
        assert len(raw)==len(expected), (rank,field,len(raw),len(expected))
        counts[field]+=len(raw)
        if field=='status':
            for role,(offset,n,stride) in roles.items():
                for worker in range(n):
                    code,=struct.unpack_from('<I',raw,offset+worker*stride)
                    status[role][code]+=1
        if raw!=expected:
            errors[field]+=1
            if field not in first:
                offset=next(i for i,(a,b) in enumerate(zip(raw,expected)) if a!=b)
                first[field]=dict(rank=rank,byte=offset,actual=raw[offset:offset+4].hex(),
                                  expected=expected[offset:offset+4].hex())
report=dict(ranks=64,bytes_checked=counts,phase_status=status,mismatched_ranks=errors,first_mismatches=first)
print(json.dumps(report,separators=(',',':')))
assert not errors, 'Unified HT Combine byte comparison failed'
