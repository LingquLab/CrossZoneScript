# Copyright (c) 2026 TileXR Project.
"""Serialize the shared DeviceView and check every expected profiling lane."""
import hashlib,json,statistics,struct
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

def summarize(case,cap,lanes=16,field="profiling"):
    intervals={}
    case=Path(case);o=layout(cap);records=[]
    for rank in range(64):
        raw=(case/'actual'/('rank%02d'%rank)/(field+'.bin')).read_bytes()
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
            for begin,end,label in ((10,11,'local_send_reduce'),(20,21,'copy_partial'),(30,31,'local_return'),(40,41,'inter_server_send'),(50,51,'final_reduce'),(60,61,'fence')):
                if cap==32 and begin in ev and end in ev:
                    duration=ts[ev.index(end)]-ts[ev.index(begin)]
                    intervals.setdefault(label,[]).append(duration)
            records.append(dict(rank=rank,lane=lane,count=n,first=ts[0],last=ts[-1],elapsed_ticks=ts[-1]-ts[0]))
    result=dict(expected_lanes=64*lanes,committed_lanes=len(records),dropped_records=0,
        global_elapsed_ticks=max(r['last'] for r in records)-min(r['first'] for r in records),
        max_lane_ticks=max(r['elapsed_ticks'] for r in records),
        median_lane_ticks=statistics.median(r['elapsed_ticks'] for r in records),
        stages={k:dict(max_lane_ticks=max(v),sum_lane_ticks=sum(v)) for k,v in intervals.items()},
        tick_frequency_hz=None,operator_us=None,conversion_status='awaiting_model_clock_evidence')
    clock_file=Path(__file__).with_name('clock_evidence.json')
    config_file=case/'v310_config/davidV100_simulator_config.yml'
    if clock_file.is_file() and config_file.is_file():
        evidence=json.loads(clock_file.read_text())
        if hashlib.sha256(config_file.read_bytes()).hexdigest()==evidence['config_sha256']:
            assert len(evidence['paired_samples'])==26 and all(q['sys_counter']==q['popped_core_tick'] for q in evidence['paired_samples'])
            scale=evidence['cycles_per_us']
            result.update(tick_frequency_hz=evidence['frequency_hz'],operator_us=result['max_lane_ticks']/scale,
                conversion_status='verified_SST_SPR_SYS_CNT_matches_core_clock',
                clock_basis_receipt=evidence['receipt_event_id'],time_scope='maximum per-AIV Begin-to-End; excludes profiling tail flush')
            for stage in result['stages'].values():
                stage['max_lane_us']=stage['max_lane_ticks']/scale
                stage['sum_lane_us']=stage['sum_lane_ticks']/scale
    (case/(field+'-records.json')).write_text(json.dumps(records,indent=2))
    (case/(field+'-summary.json')).write_text(json.dumps(result,indent=2))
    return result
