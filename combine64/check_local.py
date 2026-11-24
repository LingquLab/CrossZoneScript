# Copyright (c) 2026 TileXR Project.
"""Compare all exported local-stage payload, progress and status bytes."""
import argparse
import json
from pathlib import Path
import struct

p=argparse.ArgumentParser()
p.add_argument('action',choices=['check'])
p.add_argument('--output',type=Path,required=True)
p.add_argument('--actual',type=Path,required=True)
a=p.parse_args()
meta=json.loads((a.output/'manifest.json').read_text())
phase=meta['phase']
stats={'ranks':0,'gather_values':0,'partial_values':0,'completion_slots':0,'send_statuses':0}
for rank in range(meta['world']):
    name='rank%02d'%rank
    for field in ('returned','completed','output','status'):
        actual=(a.actual/name/(field+'.bin')).read_bytes()
        if field=='status': expected=(bytes(32)+b'\xa5'*480) if phase in ('send','fused') else b'\xa5'*512
        elif field=='output' and phase=='send': expected=(a.output/name/'initial-output.bin').read_bytes()
        else:
            expected=bytearray((a.output/name/('golden-'+field+'.bin')).read_bytes())
            if field=='completed' and phase!='send':
                for reducer in range(8): struct.pack_into('<I',expected,(meta['chunks']*8+reducer)*512,meta['chunks'])
        assert len(actual)==len(expected),(name,field,'size')
        if actual!=expected:
            width=512 if field=='completed' else 4
            at=next(i for i in range(0,len(expected),width) if actual[i:i+width]!=expected[i:i+width])
            raise ValueError('%s %s[%d] %s != %s'%(name,field,at//width,actual[at:at+8].hex(),expected[at:at+8].hex()))
        if field=='returned': stats['gather_values']+=len(expected)//4
        elif field=='output': stats['partial_values']+=len(expected)//4
        elif field=='completed': stats['completion_slots']+=len(expected)//512
        elif phase in ('send','fused'): stats['send_statuses']+=8
    stats['ranks']+=1
print(json.dumps(stats,separators=(',',':')))
