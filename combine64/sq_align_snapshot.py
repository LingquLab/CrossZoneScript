# Copyright (c) 2026 TileXR Project.
"""Stop the verified SQ-fixed bs16 joint input at 200 us and report phase and queue frontiers."""
import hashlib,json,os,shutil,signal,struct,subprocess,time
from collections import Counter
from pathlib import Path
import exports
import profile as ht_profile

ROOT=Path('/work')
BASE=ROOT/'cases/64p-t16-pipeline-a2'
CASE=ROOT/'cases/64p-t16-pipeline-sq-align-snapshot200us-a1'
STRIDE=0x2000000000
PLANE=64*40*64

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2)+'\n')

def main():
    manifest=json.loads((BASE/'case-manifest.json').read_text())
    assert manifest['candidate_commit']=='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3'
    assert json.loads((BASE/'run.json').read_text())['status']=='timed_out'
    assert manifest['kernel_sha256']=='ab2778a7435934b40925e7a959f0471ffe2fc29efeac63d47a06b51bb89a17bd'
    CASE.mkdir(exist_ok=False)
    for name,digest in manifest['files'].items():
        rel=Path(name);assert not rel.is_absolute() and '..' not in rel.parts
        src,dst=BASE/rel,CASE/rel;assert sha(src)==digest
        dst.parent.mkdir(parents=True,exist_ok=True)
        if rel.parts[0] in ('input_bin','fixture-data','kernel'):os.link(src,dst)
        else:shutil.copyfile(src,dst)
    workload=json.loads((CASE/'workload.yml').read_text().replace(str(BASE),str(CASE)))
    for rank in range(64):
        workload['workload_config']['chip%d'%rank]['output_param']['qstate']={
            'addr':rank*STRIDE+0x1b00000000,'size':5*PLANE}
    write(CASE/'workload.yml',workload)
    shutil.copyfile(CASE/'workload.yml',CASE/'v310_config/workload_config.yml')
    environment=json.loads((CASE/'sst-environment.json').read_text())
    for key in ('sst_bin','env_script','decoder_library'):
        assert sha(Path(environment[key]))==environment[key+'_sha256']
    for name,digest in environment.get('runtime_files',{}).items():assert sha(Path(name))==digest
    shell=['bash','-c','set -e; source "$1"; shift; exec "$@"','bash',environment['env_script']]
    help_result=subprocess.run(shell+[environment['sst_bin'],'--help'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=20)
    assert help_result.returncode==0 and b'--stop-at' in help_result.stdout
    record=dict(diagnostic_only=True,operator_qualified=False,stop_at='200us',wall_limit_seconds=1200,
        candidate_commit=manifest['candidate_commit'],kernel_sha256=manifest['kernel_sha256'],input_source=str(BASE),
        started_unix=time.time(),scope='identical verified SQ-aligned bs16 a2 input and object; only bounded stop and queue exports added')
    write(CASE/'diagnostic.json',record)
    command=shell+[environment['sst_bin'],'-n','8','--stop-at=200us',str(CASE/'test_UBusSim.py')]
    start=time.monotonic()
    with (CASE/'sim.log').open('wb') as log:
        child=subprocess.Popen(command,cwd=CASE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        record['pid']=child.pid;write(CASE/'diagnostic.json',record)
        try:code=child.wait(timeout=1200)
        except subprocess.TimeoutExpired:
            record['wall_timeout']=True;os.killpg(child.pid,signal.SIGTERM)
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=10)
            code=child.returncode
    record.update(sst_exit=code,sst_wall_seconds=time.monotonic()-start,finished_unix=time.time())
    write(CASE/'diagnostic.json',record)
    if code!=0:
        record['snapshot_available']=False;write(CASE/'diagnostic.json',record)
        print(json.dumps(record,separators=(',',':')),flush=True);return 1
    layout=json.loads((CASE/'export-layout.json').read_text())
    def read(rank,name,local,size):
        files=[CASE/'bin'/('david_%d_die%d_%s.bin'%(rank,die,name)) for die in (1,2)]
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
            golden=(CASE/'fixture-data'/('rank%02d'%rank)/('golden-'+field+'.bin')).read_bytes()
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
        if pending:queues.append(dict(rank=rank,pending_count=len(pending),sample=pending[:4]))
        rank_summaries.append(dict(rank=rank,status=rank_status,barrier_epochs=epochs,timeline_markers=markers))
    record.update(snapshot_available=True,status_histograms={k:dict(v) for k,v in status_hist.items()},
        barrier_histogram=dict(barriers),timeline_event_counts=dict(events),timeline_last_event_counts=dict(last_events),
        fully_matching_rank_counts=dict(matches),queue_totals=dict(queue_totals),pending_rank_count=len(queues),pending_samples=queues[:8],
        interpretation='forced stop; unmatched outputs and incomplete timelines are progress evidence, not numerical failures or pass')
    write(CASE/'rank-frontiers.json',rank_summaries)
    record['guards']=exports.verify_guards(layout,CASE/'bin',environment['decoder_library'])
    write(CASE/'diagnostic.json',record)
    summary={k:v for k,v in record.items() if k not in ('pending_samples','dump_files')}
    print(json.dumps(summary,separators=(',',':')),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
