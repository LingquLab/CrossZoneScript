from collections import Counter, defaultdict
from pathlib import Path
import csv
import hashlib
import json
import re
import struct
import sys


CASE = Path('/opt/sim/simulator/Testcases/dispatch_tilelang_64p_top6_68d32608_run01')
BUILD = Path('/work/src/dispatch_tilelang_64p_68d32608_build01')
sys.path.insert(0,str(BUILD))
from contract.runtime_profile import STAGES, summarize_lanes


def digest(data):
    return hashlib.sha256(data).hexdigest()


def agg(values):
    if not values:
        return {'cores':0,'mean_ticks':None,'max_ticks':None,'slowest':[]}
    maximum=max(ticks for _,_,ticks in values)
    worst=[[rank,core] for rank,core,ticks in values if ticks==maximum]
    return {'cores':len(values),'mean_ticks':sum(t for _,_,t in values)/len(values),
            'max_ticks':maximum,'slowest':worst[:4],'slowest_count':len(worst)}


def read_output(rank,name,size,allow_short=False):
    path=CASE/'bin'/f'david_{rank}_{name}.bin'
    data=path.read_bytes()
    if len(data)!=size and not (allow_short and len(data)==size//512*512):
        raise ValueError(f'BAD_OUTPUT_LENGTH r{rank} {name} {len(data)}/{size}')
    return data


def report(out,state):
    workload=json.loads((CASE/'tilelang-scenario.json').read_text())
    receipt=json.loads((CASE/'tilelang-run.json').read_text())
    assert receipt['status']=='PASS' and receipt['run_exit_code']==0 and receipt['rank_count']==64
    assert receipt['elf_sha']==workload['elf_sha']
    runtime=[json.loads(line) for line in (CASE/'config-load.jsonl').read_text().splitlines()]
    actual=[item for item in runtime if Path(item['directory']).name=='v310_config']
    assert actual and all(item['architecture']['chip_num']==64 and item['stars']['aicore_num']==36
                          and 'pv_simulator_config.yml' not in item['files'] for item in actual)
    assert all(item['files']['workload_config.yml'][1]==workload['workload_sha'] for item in actual)
    state.update(simulated_time=receipt['completion_times'][0],wall_s=receipt['run_wall_seconds'],
                 target_elf_sha=receipt['elf_sha'],effective_chip_count=64)
    width=14336
    route_map=defaultdict(list)
    features=[]
    for rank in range(64):
        src=CASE/'input'/f'rank{rank}'
        features.append(memoryview((src/'x.bin').read_bytes()))
        routes=struct.iter_unpack('<6i',(src/'ids.bin').read_bytes())
        for token,ids in enumerate(routes):
            for slot,expert in enumerate(ids):
                route_map[expert//6].append((rank,token,slot))
    summaries=defaultdict(list)
    rank_stats=[]
    trace=[]
    golden_fail=[]
    diagnostic_errors=[]
    issued=[]
    received=[]
    regions={}
    compare_hashes={}
    lane_count=0
    profile_issues=[]
    missing_completion=[]
    truncated_gates=[]
    for rank in range(64):
        sizes=workload['expected_sizes'][str(rank)]
        ref=CASE/'expected'/f'rank{rank}'
        buffers={name:read_output(rank,name,size) for name,size in sizes.items()}
        for name,data in buffers.items():
            compare_hashes[f'{rank}/{name}']=digest(data)
            if data!=(ref/(name+'.bin')).read_bytes():
                golden_fail.append([rank,name])
        triples=list(struct.iter_unpack('<3i',buffers['assist']))
        routes=route_map[rank]
        if Counter(triples)!=Counter(routes):
            golden_fail.append([rank,'route_coverage'])
        elif any(buffers['output'][row*width:(row+1)*width]!=features[src][token*width:(token+1)*width]
                 for row,(src,token,_) in enumerate(triples)):
            golden_fail.append([rank,'feature_provenance'])
        diag=list(struct.iter_unpack('<16i',read_output(rank,'diagnostics',4096)))
        diagnostic_errors.extend([rank,core,words[0]] for core,words in enumerate(diag) if words[0])
        issued.append(sum(words[5] for words in diag))
        received.append(sum(words[7] for words in diag))
        for name,size in (('state_count_ready',384*32),('state_consumer_gate',64*8*32),('state_sender_gate',56*32)):
            data=read_output(rank,name,size,allow_short=name=='state_sender_gate')
            if len(data)!=size: truncated_gates.append(rank)
            regions[f'{rank}/{name}']=digest(data)
            flags=struct.unpack(f'<{len(data)//4}I',data)
            if any(flags[::8] if name=='state_count_ready' else flags):
                golden_fail.append([rank,name])
        profile=read_output(rank,'profile',workload['profile_size'],allow_short=True)
        regions[f'{rank}/profile']=digest(profile)
        offsets=workload['profile_offsets']
        views=[]
        for core in range(64):
            ticks_o,ids_o,cnt_o,drop_o,done_o=offsets
            count,=struct.unpack_from('<I',profile,cnt_o+64*core)
            dropped,=struct.unpack_from('<I',profile,drop_o+64*core)
            done_at=done_o+64*core
            done=struct.unpack_from('<I',profile,done_at)[0] if done_at+4<=len(profile) else None
            if done is None:
                missing_completion.append([rank,core])
            if not (0<count<=30 and dropped==0 and done in (2,None)):
                profile_issues.append([rank,core,count,dropped,done])
                continue
            ticks=struct.unpack_from(f'<{count}Q',profile,ticks_o+256*core)
            ids=struct.unpack_from(f'<{count}I',profile,ids_o+128*core)
            views.append(dict(core=core,timestamps=ticks,event_ids=ids))
            marks=dict(zip(ids,ticks))
            try:
                checked=summarize_lanes([views[-1]])
            except ValueError as error:
                invalid_pairs = [[name,marks[start],marks[end]] for start,(name,end) in STAGES.items()
                                 if start in marks and end in marks and marks[end]<marks[start]]
                profile_issues.append([rank,core,str(error),invalid_pairs])
                continue
            if not marks[1] or marks[2]<=marks[1]:
                profile_issues.append([rank,core,'invalid whole timestamps'])
                continue
            if (400 in marks)!=(core<56) or (200 in marks)!=(core>=56) or (600 in marks)!=(core>=56) or 800 not in marks:
                profile_issues.append([rank,core,'unexpected phase assignment'])
            lane_count+=1
            for name,stat in checked.items():
                if stat['participants']:
                    summaries[name].append((rank,core,stat['mean_ticks']))
            trace.append({'name':'thread_name','ph':'M','pid':rank,'tid':core,
                          'args':{'name':f'core {core}'}})
            for start,(name,end) in STAGES.items():
                if start in marks:
                    trace.append({'name':name,'ph':'X','pid':rank,'tid':core,
                                  'ts':(marks[start]-marks[1])/1000,
                                  'dur':(marks[end]-marks[start])/1000})
        rank_stats.append({'rank':rank,'issued':issued[-1],'received':received[-1],
                           'output_rows':len(triples),'profile_lanes_read':len(views)})
    (out/'rank-work.json').write_text(json.dumps(rank_stats,indent=2)+'\n')
    (out/'output-sha.json').write_text(json.dumps({'business':compare_hashes,'regions':regions},indent=2)+'\n')
    (out/'diagnostics.json').write_text(json.dumps({'golden_fail':golden_fail,'device_errors':diagnostic_errors,
                                                   'profile_issues':profile_issues},indent=2)+'\n')
    state.update(golden_files_checked=len(compare_hashes),golden_fail=golden_fail[:12],
                 golden_failure_count=len(golden_fail),diagnostic_failure_count=len(diagnostic_errors),
                 diagnostic_examples=diagnostic_errors[:12],issued=sum(issued),received=sum(received),
                 per_rank_issued=sorted(set(issued)),per_rank_received=sorted(set(received)),
                 profile_lanes=lane_count,profile_failure_count=len(profile_issues),
                 profile_examples=profile_issues[:12],report_dir=str(out),
                 missing_completion_lanes=len(missing_completion),short_sender_gate_ranks=len(truncated_gates))
    state['modules']={k:agg(summaries[k]) for k in ('module.public_prepare','module.count_publish','module.pack_send',
                       'module.layout_prefix','module.receive_output','module.whole')}
    state['functions']={k:agg(v) for k,v in summaries.items() if re.fullmatch(r'F[0-9]\..+',k)}
    (out/'dispatch.raw-ticks.perfetto.json').write_text(json.dumps({
        'traceEvents':trace,'displayTimeUnit':'ns',
        'metadata':{'clock_alignment':'NOT_VERIFIED','axis':'raw ticks displayed at one tick per ns',
                    'origin':'independent kernel begin marker per core'}},separators=(',',':')))
    state['trace_sha256']=digest((out/'dispatch.raw-ticks.perfetto.json').read_bytes())
    state['business_status']='PASS' if (not golden_fail and not diagnostic_errors and
                                       len(compare_hashes)==256 and set(issued)==set(received)=={768}) else 'FAIL'
    state['profile_status']='PASS' if not profile_issues and not missing_completion and lane_count==4096 else 'INCOMPLETE'
    state['status']='PASS' if state['business_status']==state['profile_status']=='PASS' and not truncated_gates else 'INCOMPLETE'
    state['profile_coverage']='PARTIAL_COMPLETION_WORDS' if missing_completion else 'COMPLETE'
    state['sender_gate_coverage']='PARTIAL_RANGE' if truncated_gates else 'COMPLETE'


result={'case':str(CASE),'phase':'analysis','status':'FAIL'}
out=CASE/'analysis-03'
try:
    out.mkdir()
    report(out,result)
except Exception as error:
    import traceback
    if out.is_dir(): (out/'error.log').write_text(traceback.format_exc())
    result.update(reason=type(error).__name__+': '+str(error)[:200])
if out.is_dir(): (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,separators=(',',':')))
