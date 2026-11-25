# Copyright (c) 2026 TileXR Project.
"""Stop a verified bs16 joint case at a fixed simulated time and report progress."""
import argparse
import hashlib,json,os,shutil,signal,struct,subprocess,time
from collections import Counter
from pathlib import Path
import exports
import profile as ht_profile

ROOT=Path('/work')
STRIDE=0x2000000000
PLANE=64*40*64

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2)+'\n')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cq-followup',action='store_true')
    followup=parser.parse_args().cq_followup
    if followup:
        base=ROOT/'cases/64p-t16-pipeline-a4'
        case=ROOT/'cases/64p-t16-pipeline-a4-snapshot240us-a1'
        build=ROOT/'pipeline-four-module-sq-align-all512-cq-a1'
        stop_at='240us'
    else:
        base=ROOT/'cases/64p-t16-pipeline-a3'
        case=ROOT/'cases/64p-t16-pipeline-all-sq-align-snapshot200us-a1'
        build=ROOT/'pipeline-four-module-sq-align-all512-a1'
        stop_at='200us'
    manifest=json.loads((base/'case-manifest.json').read_text())
    assert manifest['candidate_commit']=='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3'
    run=json.loads((base/'run.json').read_text())
    assert run['status']=='timed_out' and run['kernel_sha256']==manifest['kernel_sha256']
    meta=json.loads((build/'build-result.json').read_text());binary=(build/'kernel.o').read_bytes()
    assert sha(build/'kernel.o')==meta['sha256']
    assert manifest['kernel_sha256']==hashlib.sha256(binary+bytes(10485760-len(binary))).hexdigest()
    assert sha(base/'kernel/inter_server_send.o')==manifest['kernel_sha256']
    assert '/work/inter-sq-align512-a1/inter_server_send_kernel.cpp' in (build/'pipeline_kernel.cpp').read_text()
    assert meta['compatibility_inter_source_sha256']==sha(ROOT/'inter-sq-align512-a1/inter_server_send_kernel.cpp')
    assert meta['compatibility_header_sha256']==sha(ROOT/'sq-align512-a1/include/combine_ht_kernel.h')
    if followup:
        assert manifest['kernel_sha256']=='ea66281bdd8796d68bca6b2dee16672f22f3ecd024fa7b82ddd1b98c9adfeaf6'
        api=ROOT/'cq-compiler-barrier-a2/include/tilexr/comm/udma/device/api.h'
        assert meta['compatibility_api_header_sha256']==sha(api)
    case.mkdir(exist_ok=False)
    for name,digest in manifest['files'].items():
        rel=Path(name);assert not rel.is_absolute() and '..' not in rel.parts
        src,dst=base/rel,case/rel;assert sha(src)==digest
        dst.parent.mkdir(parents=True,exist_ok=True)
        if rel.parts[0] in ('input_bin','fixture-data','kernel'):os.link(src,dst)
        else:shutil.copyfile(src,dst)
    workload=json.loads((case/'workload.yml').read_text().replace(str(base),str(case)))
    for rank in range(64):
        workload['workload_config']['chip%d'%rank]['output_param']['qstate']={
            'addr':rank*STRIDE+0x1b00000000,'size':5*PLANE}
    write(case/'workload.yml',workload)
    shutil.copyfile(case/'workload.yml',case/'v310_config/workload_config.yml')
    environment=json.loads((case/'sst-environment.json').read_text())
    for key in ('sst_bin','env_script','decoder_library'):
        assert sha(Path(environment[key]))==environment[key+'_sha256']
    for name,digest in environment.get('runtime_files',{}).items():assert sha(Path(name))==digest
    shell=['bash','-c','set -e; source "$1"; shift; exec "$@"','bash',environment['env_script']]
    help_result=subprocess.run(shell+[environment['sst_bin'],'--help'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=20)
    assert help_result.returncode==0 and b'--stop-at' in help_result.stdout
    record=dict(diagnostic_only=True,operator_qualified=False,stop_at=stop_at,wall_limit_seconds=1200,
        candidate_commit=manifest['candidate_commit'],kernel_sha256=manifest['kernel_sha256'],input_source=str(base),
        started_unix=time.time(),scope='Verified original input and object with the stop point and queue exports added')
    write(case/'diagnostic.json',record)
    command=shell+[environment['sst_bin'],'-n','8','--stop-at='+stop_at,str(case/'test_UBusSim.py')]
    start=time.monotonic()
    with (case/'sim.log').open('wb') as log:
        child=subprocess.Popen(command,cwd=case,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        record['pid']=child.pid;write(case/'diagnostic.json',record)
        try:code=child.wait(timeout=1200)
        except subprocess.TimeoutExpired:
            record['wall_timeout']=True;os.killpg(child.pid,signal.SIGTERM)
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=10)
            code=child.returncode
    record.update(sst_exit=code,sst_wall_seconds=time.monotonic()-start,finished_unix=time.time())
    write(case/'diagnostic.json',record)
    if code!=0 or record.get('wall_timeout'):
        record['snapshot_available']=False;write(case/'diagnostic.json',record)
        print(json.dumps(record,separators=(',',':')),flush=True);return 1
    layout=json.loads((case/'export-layout.json').read_text())
    def read(rank,name,local,size):
        files=[case/'bin'/('david_%d_die%d_%s.bin'%(rank,die,name)) for die in (1,2)]
        return exports.reconstruct_die_output(files,local,size,environment['decoder_library'])
    status_hist={k:Counter() for k in ('local','return','inter-send','final','fence','barrier')}
    barriers=Counter();events=Counter();last_events=Counter();queue_totals=Counter()
    queues=[];rank_summaries=[];matches=Counter();profile_layout=ht_profile.layout(32)
    for row in layout['ranks']:
        rank=row['rank'];out={}
        for name,target in row['exports'].items():
            out[name]=read(rank,name,target['addr']-rank*STRIDE,target['size'])
        rank_status={}
        for label,offset,count,stride in (('local',0,8,4),('return',512,8,32),('inter-send',1024,16,32),
                ('final',1536,16,32),('fence',2048,1,32),('barrier',2560,16,32)):
            values=[struct.unpack_from('<I',out['status'],offset+i*stride)[0] for i in range(count)]
            status_hist[label].update(values);rank_status[label]=dict(Counter(values))
        epochs=[struct.unpack_from('<I',out['barriers'],i*512)[0] for i in range(16)]
        barriers.update(epochs)
        markers=[]
        for lane in range(16):
            values=struct.unpack_from('<32I',out['profiling'],profile_layout['events']+lane*32*4)
            ticks=struct.unpack_from('<32Q',out['profiling'],profile_layout['timestamps']+lane*32*8)
            nonzero=[(event,tick) for event,tick in zip(values,ticks) if event]
            events.update(event for event,tick in nonzero)
            last_events[nonzero[-1][0] if nonzero else 0]+=1
            markers.append(nonzero)
        for field in ('gather','partial','returned','completed','output'):
            golden=(case/'fixture-data'/('rank%02d'%rank)/('golden-'+field+'.bin')).read_bytes()
            if out[field]==golden:matches[field]+=1
        qstate=read(rank,'qstate',0x1b00000000,5*PLANE)
        pending=[]
        for peer in range(64):
            for physical in range(40):
                flat=peer*40+physical
                values={key:struct.unpack_from('<I',qstate,plane*PLANE+flat*64)[0]
                    for plane,key in enumerate(('count','sqhead','sqtail','cqhead','cqtail'))}
                if any(values.values()):
                    queue_totals.update(values);queue_totals['active_queues']+=1
                    if values['sqhead']!=values['sqtail']:
                        pending.append(dict(peer=peer,physical=physical,**values))
                        queue_totals['pending_local' if physical%20>=16 else 'pending_inter_return']+=1
        queue_totals['pending_queues']+=len(pending)
        if pending:queues.append(dict(rank=rank,pending_count=len(pending),sample=pending))
        rank_summaries.append(dict(rank=rank,status=rank_status,barrier_epochs=epochs,timeline_markers=markers))
    record.update(snapshot_available=True,status_histograms={k:dict(v) for k,v in status_hist.items()},
        barrier_histogram=dict(barriers),timeline_event_counts=dict(events),timeline_last_event_counts=dict(last_events),
        fully_matching_rank_counts=dict(matches),queue_totals=dict(queue_totals),pending_rank_count=len(queues),pending_samples=queues[:8],
        interpretation='forced stop; unmatched outputs and incomplete timelines are progress evidence, not numerical failures or pass')
    write(case/'rank-frontiers.json',rank_summaries)
    write(case/'pending-queues.json',queues)
    record['pending_rank8_queue_count']=next((q['pending_count'] for q in queues if q['rank']==8),0)
    record['guards']=exports.verify_guards(layout,case/'bin',environment['decoder_library'])
    write(case/'diagnostic.json',record)
    summary={k:v for k,v in record.items() if k not in ('pending_samples','dump_files')}
    print(json.dumps(summary,separators=(',',':')),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
