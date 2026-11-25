"""Bounded unchanged-bs16-send reproduction with queue and CQ exports only."""
import json,os,shutil,signal,struct,subprocess,time
from pathlib import Path
import exports

ROOT=Path('/work')
BASE=ROOT/'cases/64p-t16-local-send-a1'
CASE=ROOT/'cases/64p-t16-send-cq-snapshot40us-a1'
STRIDE=0x2000000000
PLANE=64*40*64
def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def cq_address(rank,gateway):
    peer=rank//8*8+gateway;physical=gateway%2*20+gateway//2
    return 0x1a00000000+(peer*40+physical)*16384*64
def main():
    manifest=json.loads((BASE/'case-manifest.json').read_text())
    assert manifest['candidate_commit']=='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3'
    assert manifest['kernel_sha256']=='d81736cc8426400a51126b01868a01ee7d9b1fb127003185eebb7d7f4c175481'
    assert json.loads((BASE/'run.json').read_text())['status']=='timed_out'
    CASE.mkdir(exist_ok=False)
    for name,digest in manifest['files'].items():
        rel=Path(name);assert not rel.is_absolute() and '..' not in rel.parts
        src,dst=BASE/rel,CASE/rel;assert exports.digest(src)==digest
        dst.parent.mkdir(parents=True,exist_ok=True)
        if rel.parts[0] in ('input_bin','fixture-data','kernel'):os.link(src,dst)
        else:shutil.copyfile(src,dst)
    workload=json.loads((CASE/'workload.yml').read_text().replace(str(BASE),str(CASE)))
    for rank in range(64):
        out=workload['workload_config']['chip%d'%rank]['output_param']
        out['qstate']=dict(addr=rank*STRIDE+0x1b00000000,size=5*PLANE)
        for g in range(8):out['cq%d'%g]=dict(addr=rank*STRIDE+cq_address(rank,g),size=1024)
    write(CASE/'workload.yml',workload);shutil.copyfile(CASE/'workload.yml',CASE/'v310_config/workload_config.yml')
    env=json.loads((CASE/'sst-environment.json').read_text())
    for key in ('sst_bin','env_script','decoder_library'):assert exports.digest(env[key])==env[key+'_sha256']
    for name,digest in env.get('runtime_files',{}).items():assert exports.digest(name)==digest
    record=dict(diagnostic_only=True,operator_qualified=False,input_source=str(BASE),candidate_commit=manifest['candidate_commit'],
        kernel_sha256=manifest['kernel_sha256'],stop_at='40us',wall_limit_seconds=900,started_unix=time.time(),
        scope='Unchanged original send object and inputs; only stop-at and queue/CQ output ranges added')
    write(CASE/'diagnostic.json',record)
    cmd=['bash','-c','set -e; source "$1"; shift; exec "$@"','bash',env['env_script'],env['sst_bin'],'-n','8','--stop-at=40us',str(CASE/'test_UBusSim.py')]
    start=time.monotonic()
    with (CASE/'sim.log').open('wb') as log:
        child=subprocess.Popen(cmd,cwd=CASE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        record['pid']=child.pid;write(CASE/'diagnostic.json',record)
        try:code=child.wait(timeout=900)
        except subprocess.TimeoutExpired:
            record['wall_timeout']=True;os.killpg(child.pid,signal.SIGTERM)
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=10)
            code=child.returncode
    record.update(sst_exit=code,sst_wall_seconds=time.monotonic()-start,finished_unix=time.time())
    write(CASE/'diagnostic.json',record)
    if code or record.get('wall_timeout'):
        print(json.dumps(record,separators=(',',':')),flush=True);return 1
    rows=[]
    def read(rank,name,address,size):
        paths=[CASE/'bin'/('david_%d_die%d_%s.bin'%(rank,die,name)) for die in (1,2)]
        return exports.reconstruct_die_output(paths,address,size,env['decoder_library'])
    for rank in range(64):
        q=read(rank,'qstate',0x1b00000000,5*PLANE)
        for g in range(8):
            peer=rank//8*8+g;physical=g%2*20+g//2;flat=peer*40+physical
            counts={k:struct.unpack_from('<I',q,i*PLANE+flat*64)[0] for i,k in enumerate(('count','sqhead','sqtail','cqhead','cqtail'))}
            if not any(counts.values()):continue
            local=cq_address(rank,g);raw=read(rank,'cq%d'%g,local,1024)
            entries=[]
            for i in range(16):
                first,second=struct.unpack_from('<II',raw,i*64)
                entries.append(dict(index=i,owner=(first>>2)&1,status=first>>24,substatus=(first>>16)&255,entry_index=second&65535))
            copies=[exports.read_addressed(CASE/'bin'/('david_%d_die%d_cq%d.bin'%(rank,die,g)),local,1024) for die in (1,2)]
            owners=[[((struct.unpack_from('<I',copy,i*64)[0]>>2)&1) for i in range(16)] for copy in copies]
            rows.append(dict(rank=rank,gateway=g,cq_local=local,**counts,entries=entries,raw_die_owner_bits=owners))
    write(CASE/'all-queues.json',rows)
    pending=[row for row in rows if row['sqhead']!=row['sqtail']]
    record.update(snapshot_available=True,active_queues=len(rows),pending_count=len(pending),pending=pending[:16],
        completed_over8_samples=[row for row in rows if row['sqhead']>8 and row['sqhead']==row['sqtail']][:3],
        interpretation='Forced-stop diagnostic, not an operator validation or normal latency')
    write(CASE/'diagnostic.json',record)
    print(json.dumps(record,separators=(',',':')),flush=True);return 0
if __name__=='__main__':raise SystemExit(main())
