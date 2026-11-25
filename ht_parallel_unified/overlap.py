"""Pair trace events by rank and route for the unified T4 run."""
from collections import Counter
import json
import struct

RANK_STRIDE = 0x2000000000
LIMIT = 64 * 1024 * 1024
WRITES = {'WR_REQ', 'WR_DATA', 'WR_REQ_RESP', 'WR_DATA_RESP'}
RANKS = range(64)


def integer(value):
    return int(value,0) if isinstance(value,str) else int(value)


def events(path):
    if path.stat().st_size > LIMIT:
        return None
    parsed = json.loads(path.read_bytes())
    return parsed['traceEvents'] if isinstance(parsed,dict) else parsed


def remote_owner(rank):
    peer = rank ^ 8
    group = ((peer//8) + 4 - (rank//8)%4)%4
    position = 8 + (group-1)*16 + (peer//32)*8
    return (position+8)%16 if position<48 else 0


def address_plan(case, rank, offsets):
    workspace = offsets['workspace'] % RANK_STRIDE
    output = 0x406000000
    partial = workspace+offsets['partial_offset']
    ready = workspace+offsets['ready_offset']
    returned = workspace+offsets['return_offset']
    targets = {}
    for token in range(4):
        for route in ('local','cross'):
            target = partial + (token+4*(route=='cross'))*512
            targets[target] = ('partial_'+route,token)
    for reducer in range(8):
        targets[ready+reducer*512] = ('ready',reducer)
    targets[returned+(rank//8)*512] = ('local_return0',0)
    targets[returned+((rank//8)^1)*512] = ('cross_return0_arrival',0)
    for token in range(4):
        targets[output+token*512] = ('final_output',token)
    context = case/'input_bin'/f'rank{rank:02d}'/'native_sq_ctx.bin'
    if context.is_file():
        first = ((rank^8)*32+remote_owner(rank)*2)*80
        sq, = struct.unpack_from('<Q',context.read_bytes(),first+8)
        targets[sq%RANK_STRIDE] = ('cross_return0_sq',0)
    return targets


def collect(case, offsets):
    counts = Counter()
    samples = {}
    timestamps = {}
    for rank in RANKS:
        path=case/'OUTPUT/stat_trace'/f'Host{rank}_ba_layer_trace.json'
        if not path.is_file():
            continue
        counts['files']+=1
        try:
            items=events(path)
        except (ValueError,KeyError,TypeError):
            counts['malformed']+=1
            continue
        if items is None:
            counts['truncated']+=1
            continue
        targets=address_plan(case,rank,offsets[rank])
        per_rank={}
        for event in items:
            if not isinstance(event,dict):
                continue
            data=event.get('args',{})
            action=data.get('busPayloadType_')
            if action not in WRITES or not isinstance(event.get('ts'),(int,float)):
                continue
            ts=event['ts']
            # Prefer the original globally addressed memory transaction.
            for field in ('oriAddr_','wrAddr_'):
                try:
                    host,offset=divmod(integer(data[field]),RANK_STRIDE)
                except (KeyError,ValueError,TypeError):
                    continue
                if host not in (rank,0):
                    continue
                item=targets.get(offset//512*512)
                if item and offset%512==0:
                    name,index=item
                    per_rank.setdefault(name,{}).setdefault(index,{}).setdefault(action,set()).add(ts)
                    counts[name]+=1
                    break
        timestamps[rank]=per_rank
        if rank<2:
            samples[str(rank)]={key:sorted({action for item in by_index.values() for action in item})
                                for key,by_index in per_rank.items()}
    return counts,timestamps,samples


def compare(timestamps, destination_key, partial_key, mapped_source):
    matched=0
    earlier=0
    examples=[]
    for dest in RANKS:
        src=mapped_source(dest)
        dest_trace=timestamps.get(dest,{}).get(destination_key,{}).get(0,{})
        source_trace=timestamps.get(src,{}).get(partial_key,{})
        if not all(token in source_trace for token in (1,2,3)):
            continue
        for action in ('WR_DATA','WR_REQ'):
            if action in dest_trace and all(action in source_trace[token] for token in (1,2,3)):
                first=min(dest_trace[action])
                later=[min(source_trace[token][action]) for token in (1,2,3)]
                matched+=1
                earlier+=first<max(later)
                if len(examples)<2:
                    examples.append(dict(source=src,destination=dest,event=action,return0=first,later_partial=later))
                break
    return dict(matched=matched,return0_earlier=earlier,examples=examples)


def summarize_saved_overlap(case):
    summary={}
    result=case/'unified-result.json'
    if result.is_file():
        saved=json.loads(result.read_text())
        summary['run']={k:saved[k] for k in ('status','sim_us','cqe','runner_exit_code','checked','guards','checker_detail')
                        if k in saved}
    path=case/'fixture-data/trace-offsets.json'
    if not path.is_file():
        return summary
    counts,timestamps,samples=collect(case,json.loads(path.read_text()))
    summary['counts']=counts
    summary['local']=compare(timestamps,'local_return0','partial_local',lambda r:r)
    summary['cross_arrival']=compare(timestamps,'cross_return0_arrival','partial_cross',lambda r:r^8)
    summary['cross_sq']=compare(timestamps,'cross_return0_sq','partial_cross',lambda r:r)
    summary['sample_event_kinds']=samples
    return summary
