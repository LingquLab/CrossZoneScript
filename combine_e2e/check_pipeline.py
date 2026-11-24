# Copyright (c) 2026 TileXR Project.
"""Check every stage's exported data and all status bytes from one pipeline launch."""
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('action',choices=['check'])
p.add_argument('--output',type=Path,required=True);p.add_argument('--actual',type=Path,required=True)
a=p.parse_args();meta=json.loads((a.output/'manifest.json').read_text())
assert meta['joint_single_invocation'] is True
stats={'ranks':0,'gather_values':0,'partial_values':0,'returned_values':0,'final_values':0,'status_codes':0,'flags_bytes':0}
for rank in range(meta['world']):
    name='rank%02d'%rank
    for field in ('status','barriers','gather','partial','returned','completed','output'):
        actual=(a.actual/name/(field+'.bin')).read_bytes()
        expected=(a.output/name/('golden-'+field+'.bin')).read_bytes()
        assert len(actual)==len(expected),(name,field,'size')
        if actual!=expected:
            at=next(i for i in range(0,len(expected),4) if actual[i:i+4]!=expected[i:i+4])
            detail=''
            if field=='status':detail=' stage='+('local','return','inter-send','final','fence','barrier')[at//512]
            raise ValueError('%s %s[%d]%s %s != %s'%(name,field,at//4,detail,actual[at:at+8].hex(),expected[at:at+8].hex()))
        if field in ('gather','partial','returned'):stats[field+'_values']+=len(expected)//4
        elif field=='output':stats['final_values']+=len(expected)//4
        elif field=='status':stats['status_codes']+=65
        else:stats['flags_bytes']+=len(expected)
    stats['ranks']+=1
print(json.dumps(stats,separators=(',',':')))
