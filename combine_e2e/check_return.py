# Copyright (c) 2026 TileXR Project.
"""Validate every returned value, notification, final value and status slot."""
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('action',choices=['check'])
p.add_argument('--output',type=Path,required=True)
p.add_argument('--actual',type=Path,required=True)
a=p.parse_args()
meta=json.loads((a.output/'manifest.json').read_text())
stats={'ranks':0,'returned_values':0,'completion_bytes':0,'output_values':0,'statuses':0}
for rank in range(meta['world']):
    name='rank%02d'%rank
    for field in ('returned','completed','output','status'):
        actual=(a.actual/name/(field+'.bin')).read_bytes()
        expected=(a.output/name/('golden-'+field+'.bin')).read_bytes()
        assert len(actual)==len(expected),(name,field,'size')
        if actual!=expected:
            offset=next(i for i in range(0,len(expected),4) if actual[i:i+4]!=expected[i:i+4])
            raise ValueError('%s %s[%d] %s != %s'%(name,field,offset//4,actual[offset:offset+8].hex(),expected[offset:offset+8].hex()))
        if field=='returned':stats['returned_values']+=len(expected)//4
        elif field=='completed':stats['completion_bytes']+=len(expected)
        elif field=='output':stats['output_values']+=len(expected)//4
        else:stats['statuses']+=meta['blocks']
    stats['ranks']+=1
print(json.dumps(stats,separators=(',',':')))
