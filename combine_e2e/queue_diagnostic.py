# Copyright (c) 2026 TileXR Project.
"""Stop a fresh sender simulation at a bounded time and inspect queue frontiers."""
import hashlib,importlib.util,json,os,re,shutil,signal,struct,subprocess,time
from pathlib import Path

ROOT=Path('/work')
BASE=ROOT/'cases/64p-t16-local-send-a1'
CASE=ROOT/'cases/64p-t16-send-queue-stop40us-a1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def main(directory):
    directory=Path(directory)
    manifest=json.loads((BASE/'case-manifest.json').read_text())
    assert manifest['candidate_commit']=='013cc3e2f8abf8157abdc609afcfe2369806b1e1'
    assert json.loads((BASE/'run.json').read_text())['status']=='timed_out'
    CASE.mkdir(exist_ok=False)
    for name,digest in manifest['files'].items():
        rel=Path(name)
        assert not rel.is_absolute() and '..' not in rel.parts
        src,dst=BASE/rel,CASE/rel
        assert sha(src)==digest
        dst.parent.mkdir(parents=True,exist_ok=True)
        if rel.parts[0] in ('input_bin','fixture-data','kernel'):os.link(src,dst)
        else:shutil.copyfile(src,dst)
    environment=json.loads((CASE/'sst-environment.json').read_text())
    for key in ('sst_bin','env_script','decoder_library'):
        assert sha(Path(environment[key]))==environment[key+'_sha256']
    for name,digest in environment.get('runtime_files',{}).items():assert sha(Path(name))==digest
    help_command=['bash','-c','set -e; source "$1"; shift; exec "$@"','bash',environment['env_script'],environment['sst_bin'],'--help']
    help_result=subprocess.run(help_command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=20)
    assert help_result.returncode==0 and b'--stop-at' in help_result.stdout,'installed_sst_has_no_stop_at'
    workload=json.loads((CASE/'workload.yml').read_text().replace(str(BASE),str(CASE)))
    queues=[]
    stride=0x2000000000
    plane_bytes=64*40*64
    for rank in range(64):
        base=rank*stride
        peer_begin=rank//8*8
        chip=workload['workload_config']['chip%d'%rank]
        params=chip['output_param']
        row={'rank':rank,'planes':{},'cqs':{}}
        for index,key in enumerate(('qcount','sqhead','sqtail','cqhead','cqtail')):
            target={'addr':base+0x1b00000000+index*plane_bytes+peer_begin*40*64,'size':8*40*64}
            params[key]=target
            row['planes'][key]=target
        prefill=(CASE/'input_bin'/('rank%02d'%rank)/'prefill.bin').read_bytes()
        _,_,groups,chunks=struct.unpack_from('<4I',prefill)
        for gateway in range(8):
            if gateway==rank%8:continue
            expected=0
            for stage in range(groups*chunks):
                _,count,_,notify,_=struct.unpack_from('<3Q2I',prefill,512+(gateway*groups*chunks+stage)*32)
                expected+=count+(notify!=0)
            physical=(gateway%2)*20+gateway//2
            flat=(peer_begin+gateway)*40+physical
            count=(expected+8+7)//8*8
            target={'addr':base+0x1a00000000+flat*16384*64,'size':max(512,count*64)}
            params['cq%d'%gateway]=target
            row['cqs'][str(gateway)]=dict(target,expected=expected,physical=physical)
        queues.append(row)
    write(CASE/'workload.yml',workload)
    shutil.copyfile(CASE/'workload.yml',CASE/'v310_config/workload_config.yml')
    write(CASE/'queue-layout.json',queues)
    record={'diagnostic_only':True,'operator_qualified':False,'stop_at':'40us',
            'candidate_commit':manifest['candidate_commit'],'kernel_sha256':manifest['kernel_sha256'],
            'input_source':str(BASE),'started_unix':time.time()}
    write(CASE/'diagnostic.json',record)
    command=['bash','-c','set -e; source "$1"; shift; exec "$@"','bash',environment['env_script'],
             environment['sst_bin'],'-n','8','--stop-at=40us',str(CASE/'test_UBusSim.py')]
    start=time.monotonic()
    with (CASE/'sim.log').open('wb') as log:
        child=subprocess.Popen(command,cwd=str(CASE),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        record['pid']=child.pid
        write(CASE/'diagnostic.json',record)
        try:code=child.wait(timeout=360)
        except subprocess.TimeoutExpired:
            record['wall_timeout']=True
            os.killpg(child.pid,signal.SIGTERM)
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=10)
            code=child.returncode
    record.update(exit_code=code,wall_seconds=time.monotonic()-start)
    spec=importlib.util.spec_from_file_location('queue_exports',str(directory/'exports.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def read(rank,key,target):
        paths=[CASE/'bin'/('david_%d_die%d_%s.bin'%(rank,die,key)) for die in (1,2)]
        return module.reconstruct_die_output(paths,target['addr']-rank*stride,target['size'],environment['decoder_library'])
    if code==0:
        pending=[]
        totals={'expected':0,'sqhead':0,'sqtail':0,'cqtail':0,'qcount':0}
        for row in queues:
            rank=row['rank']
            planes={key:read(rank,key,target) for key,target in row['planes'].items()}
            for key,target in row['cqs'].items():
                gateway=int(key)
                at=(gateway*40+target['physical'])*64
                counters={name:struct.unpack_from('<I',data,at)[0] for name,data in planes.items()}
                cq=read(rank,'cq'+key,target)
                tail=counters['cqtail']
                current=cq[tail*64:(tail+1)*64]
                head,tail_sq=counters['sqhead'],counters['sqtail']
                totals['expected']+=target['expected']
                for name in ('sqhead','sqtail','cqtail','qcount'):totals[name]+=counters[name]
                if head!=target['expected'] or tail_sq!=head:
                    item=dict(rank=rank,gateway=gateway,expected=target['expected'],**counters)
                    if len(current)>=8:
                        word,entry=struct.unpack_from('<II',current)
                        item.update(next_owner=(word>>2)&1,next_status=word>>24,next_substatus=(word>>16)&255,next_entry=entry&65535)
                    pending.append(item)
        record.update(queue_totals=totals,pending_queues=pending[:24],pending_queue_count=len(pending))
        layout=json.loads((CASE/'export-layout.json').read_text())
        record['guards']=module.verify_guards(layout,CASE/'bin',environment['decoder_library'])
        module.export_actual(layout,CASE/'bin',CASE/'actual-diagnostic',environment['decoder_library'])
        p=subprocess.run(['python3',str(directory/'check_local.py'),'check','--output',str(CASE/'fixture-data'),
                          '--actual',str(CASE/'actual-diagnostic')],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        record['numerical_checker_exit']=p.returncode
        if p.returncode==0:record['checked']=json.loads(p.stdout)
        else:
            errors=[s[:250] for s in p.stderr.decode(errors='replace').splitlines() if s.startswith(('ValueError:','AssertionError:'))]
            record['first_error']=errors[-1] if errors else 'see_diagnostic_check_log'
        (CASE/'check.stdout').write_bytes(p.stdout)
        (CASE/'check.stderr').write_bytes(p.stderr)
    else:record['queue_dump_available']=False
    record['diagnostic_finished']=True
    write(CASE/'diagnostic.json',record)
    print(json.dumps(record,separators=(',',':')))
    return 0

if __name__=='__main__':
    import sys
    raise SystemExit(main(sys.argv[1]))
