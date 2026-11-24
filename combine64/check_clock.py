# Copyright (c) 2026 TileXR Project.
"""Validate the diagnostic timer interval and each rank's completion code."""
import argparse,json,struct
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('action',choices=['check']);p.add_argument('--output',required=True,type=Path);p.add_argument('--actual',required=True,type=Path)
a=p.parse_args();meta=json.loads((a.output/'manifest.json').read_text());durations=[]
for rank in range(64):
    folder=a.actual/('rank%02d'%rank)
    begin,end,elapsed,status=[struct.unpack('<Q',(folder/(name+'.bin')).read_bytes())[0] for name in ('returned','completed','output','status')]
    assert 0<begin<end and elapsed==end-begin and meta['wait_ticks']<=elapsed<=meta['wait_ticks']+1000 and status==0,(rank,begin,end,elapsed,status)
    durations.append(elapsed)
print(json.dumps({'ranks':64,'diagnostic_only':True,'min_elapsed_ticks':min(durations),'max_elapsed_ticks':max(durations)},separators=(',',':')))
