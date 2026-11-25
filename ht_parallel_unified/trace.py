"""Extract only timing evidence for chunk 0 returns and the later producer writes."""
from collections import Counter
import re

TRACE_ARGS = re.compile(rb'\{[^{}]*"busPayloadType_"[^{}]*\}')
TS = re.compile(rb'"ts"\s*:\s*(-?\d+(?:\.\d+)?)')
LIMIT = 32 * 1024 * 1024


def address(value):
    return int(value,0) if isinstance(value,str) else int(value)


def events(path):
    with path.open('rb') as stream:
        raw=stream.read(LIMIT)
        clipped=bool(stream.read(1))
    timestamps=list(TS.finditer(raw))
    times=iter(timestamps)
    following=next(times,None)
    previous=None
    for match in TRACE_ARGS.finditer(raw):
        while following and following.start()<match.start():
            previous=following
            following=next(times,None)
        event=json.loads(match.group())
        if event.get('busPayloadType_')!='WR_REQ':
            continue
        if previous is None or match.start()-previous.end()>4096:
            continue
        for key in ('oriAddr_','wrAddr_'):
            if event.get(key) is not None:
                try:
                    yield address(event[key]),float(previous.group(1)),clipped
                    break
                except (TypeError,ValueError):
                    pass


def summarize_trace(case):
    offsets=case/'fixture-data/trace-offsets.json'
    if not offsets.is_file():
        return {'status':'fixture_offsets_unavailable'}
    layout=json.loads(offsets.read_text())
    results, observed, files, truncated = {}, Counter(), 0, 0
    gm=0x2000000000
    trace_root=case/'OUTPUT/stat_trace'
    samples={}
    for rank in range(64):
        path=trace_root/f'Host{rank}_ba_layer_trace.json'
        if not path.is_file():
            continue
        files+=1
        truncated+=path.stat().st_size>LIMIT
        targets={}
        for token in range(4):
            targets[layout[rank]['partial_offset']+token*512]=('partial_local',token)
            targets[layout[rank]['partial_offset']+(token+4)*512]=('partial_cross',token)
        for reducer in range(8):
            targets[layout[rank]['ready_offset']+reducer*512]=('ready',reducer)
        for server in (rank//8,(rank//8)^1):
            targets[layout[rank]['return_offset']+server*512]=('return0',server)
        times={}
        workspace=layout[rank]['workspace']
        for addr,ts,_ in events(path):
            offset=(addr-workspace)%gm
            item=targets.get(offset//512*512)
            if item is None:
                continue
            key,value=item
            observed[key]+=1
            times.setdefault(key,{}).setdefault(value,[]).append(ts)
        if len(samples)<2 and times:
            samples[str(rank)]={phase:[min(stamps[0] for stamps in items.values()),
                                      max(max(s) for s in items.values())]
                                for phase,items in times.items()}
        if 'return0' in times and 'partial_local' in times:
            dest=times['return0'].get(rank//8)
            later=[min(stamps) for token,stamps in times['partial_local'].items() if token>0]
            if dest and later:
                observed['ranks_with_local_comparison']+=1
                if min(dest)<max(later):
                    observed['ranks_local_chunk0_before_later_partial']+=1
                    results.setdefault('local_sample',[rank,min(dest),max(later)])
        if 'return0' in times and 'partial_cross' in times:
            # The cross return arriving here originates at rank ^ 8.
            results.setdefault('cross_first_destination',[rank,min(times['return0'].get((rank//8)^1,[float('inf')]))])
    if any(not value[1]<float('inf') for key,value in list(results.items()) if key=='cross_first_destination'):
        del results['cross_first_destination']
    return dict(files=files,truncated=truncated,address_hits=dict(observed),rank_samples=samples,**results)
