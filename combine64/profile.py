# Copyright (c) 2026 TileXR Project.
"""Serialize the shared DeviceView and check every expected profiling lane."""
import json,statistics,struct
from pathlib import Path
LANES=16
STATE_STRIDE=16
ADDRESS=0x430000000

def layout(cap):
    t=512;e=t+LANES*cap*8;c=e+LANES*cap*4;d=c+LANES*64;s=d+LANES*64
    return dict(timestamps=t,events=e,counts=c,dropped=d,states=s,bytes=s+LANES*64,capacity=cap)

def image(address,cap):
    o=layout(cap);raw=bytearray(o['bytes'])
    struct.pack_into('<8I7Q',raw,0,2,88,1,LANES,cap,cap,STATE_STRIDE,0,
        address+o['timestamps'],address+o['events'],address+o['counts'],address+o['dropped'],address+o['states'],0,1)
    return bytes(raw)

def summarize(case,cap,lanes=16):
    case=Path(case);o=layout(cap);records=[]
    for rank in range(64):
        raw=(case/'actual'/('rank%02d'%rank)/'profiling.bin').read_bytes()
        assert len(raw)==o['bytes'],'profiling_size'
        for lane in range(lanes):
            at=lane*64
            n=struct.unpack_from('<I',raw,o['counts']+at)[0]
            lost=struct.unpack_from('<I',raw,o['dropped']+at)[0]
            state=struct.unpack_from('<I',raw,o['states']+at)[0]
            assert state==2 and lost==0 and 2<=n<=cap,('profile_state',rank,lane,n,lost,state)
            ts=struct.unpack_from('<%dQ'%n,raw,o['timestamps']+lane*cap*8)
            ev=struct.unpack_from('<%dI'%n,raw,o['events']+lane*cap*4)
            assert ev[0]==1 and ev[-1]==2 and all(a<=b for a,b in zip(ts,ts[1:])),('profile_events',rank,lane)
            records.append(dict(rank=rank,lane=lane,count=n,first=ts[0],last=ts[-1],elapsed_ticks=ts[-1]-ts[0]))
    result=dict(expected_lanes=64*lanes,committed_lanes=len(records),dropped_records=0,
        global_elapsed_ticks=max(r['last'] for r in records)-min(r['first'] for r in records),
        max_lane_ticks=max(r['elapsed_ticks'] for r in records),
        median_lane_ticks=statistics.median(r['elapsed_ticks'] for r in records),
        tick_frequency_hz=None,operator_us=None,conversion_status='awaiting_model_clock_evidence')
    (case/'profiling-records.json').write_text(json.dumps(records,indent=2))
    (case/'profiling-summary.json').write_text(json.dumps(result,indent=2))
    return result
