# Copyright (c) 2026 TileXR Project.
"""Compare a compiler-only CQ cache barrier overlay; never launch SST here."""
import hashlib,json,struct,subprocess,time
from collections import Counter
from pathlib import Path
import exports
import profile as ht_profile

ROOT=Path('/work')
HEADER_SHA='44d0dd9097b4f154972e971c26fccb2b51a15d559b847d8f98d020a40516f881'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2)+'\n')

def text_section(path):
    raw=path.read_bytes();offset=struct.unpack_from('<Q',raw,40)[0]
    size,count,names=struct.unpack_from('<HHH',raw,58)
    rows=[struct.unpack_from('<IIQQQQIIQQ',raw,offset+i*size) for i in range(count)]
    table=rows[names];strings=raw[table[4]:table[4]+table[5]]
    for row in rows:
        if strings[row[0]:strings.find(bytes([0]),row[0])]==b'.text':return raw[row[4]:row[4]+row[5]]
    raise ValueError('ELF text section missing')

def postmortem():
    observations=[]
    for name in ('64p-t16-local-send-a1','64p-t16-pipeline-a1'):
        case=ROOT/'cases'/name
        run=json.loads((case/'run.json').read_text());assert run['status']=='timed_out'
        row=dict(case=name,run_status=run['status'],dump_file_count=len(list((case/'bin').glob('*.bin'))),operator_qualified=False)
        observations.append(row)
        if not row['dump_file_count']:continue
        layout=json.loads((case/'export-layout.json').read_text())
        env=json.loads((case/'sst-environment.json').read_text())
        statuses={label:Counter() for label in ('local','return','inter-send','final','barrier')}
        epochs=Counter();global_last=Counter();native_last=Counter();native_states=Counter();errors=[]
        for item in layout['ranks']:
            rank=item['rank'];base=item['sys_va_base_addr']
            def read(field):
                target=item['exports'][field]
                files=[case/'bin'/('david_%d_die%d_%s.bin'%(rank,die,field)) for die in (1,2)]
                return exports.reconstruct_die_output(files,target['addr']-base,target['size'],env['decoder_library'])
            try:
                status=read('status')
                if name.endswith('pipeline-a1'):
                    for label,offset,count,stride in (('local',0,8,4),('return',512,8,32),('inter-send',1024,16,32),('final',1536,16,32),('barrier',2560,16,32)):
                        statuses[label].update(struct.unpack_from('<I',status,offset+i*stride)[0] for i in range(count))
                    barrier=read('barriers');epochs.update(struct.unpack_from('<I',barrier,i*512)[0] for i in range(16))
                    captures=(('profiling',32,global_last),('local_profiling',8192,native_last))
                else:
                    statuses['local'].update(struct.unpack_from('<I',status,i*4)[0] for i in range(8))
                    captures=(('profiling',8192,native_last),)
                for field,capacity,histogram in captures:
                    raw=read(field);p=ht_profile.layout(capacity)
                    for lane in range(16):
                        values=struct.unpack_from('<%dI'%capacity,raw,p['events']+lane*capacity*4)
                        last=next((v for v in reversed(values) if v),0);histogram[last]+=1
                        if capacity==8192:native_states[struct.unpack_from('<I',raw,p['states']+lane*64)[0]]+=1
            except (OSError,ValueError) as error:
                errors.append(dict(rank=rank,error=str(error)[:180]))
        row.update(status_histograms={k:dict(v) for k,v in statuses.items()},barrier_epochs=dict(epochs),
            timeline_last_observed_event=dict(global_last),native_last_observed_event=dict(native_last),
            native_profile_state_histogram=dict(native_states),decode_error_count=len(errors),decode_error_sample=errors[:2],
            scope='saved timeout dumps only; unfinished cached markers are incomplete evidence, never operator pass')
    return observations

def main():
    dest=ROOT/'cq-compiler-barrier-a1';dest.mkdir(exist_ok=False)
    header=ROOT/'source/include/tilexr/comm/udma/device/api.h'
    assert sha(header)==HEADER_SHA
    original=header.read_text()
    first=original.index('__aicore__ inline void UDMACleanCacheLines(')
    last=original.index('__aicore__ inline __gm__ UDMAWQCtx* UDMAGetWQCtx(',first)
    fragment=original[first:last];needle='__asm__ __volatile__("");'
    assert fragment.count(needle)==2
    modified=original[:first]+fragment.replace(needle,'__asm__ __volatile__("" ::: "memory");')+original[last:]
    overlay=dest/'include/tilexr/comm/udma/device/api.h';overlay.parent.mkdir(parents=True);overlay.write_text(modified)
    baseline=json.loads((ROOT/'profile-build/result.json').read_text())['targets']['local_ht']
    binary=ROOT/baseline['path'];assert sha(binary)==baseline['sha256']
    compile_cmd=list(baseline['commands'][0]);assert compile_cmd[-2]=='-o'
    old_object=compile_cmd[-1];new_object=str(dest/'local_ht.rel.o')
    compile_cmd[-1]=new_object;compile_cmd.insert(1,'-I'+str(dest/'include'))
    linked=dest/'local_ht.o'
    link_cmd=[new_object if v==old_object else str(linked) if v==str(binary) else v for v in baseline['commands'][1]]
    assert link_cmd[-2:]==['-o',str(linked)]
    report=dict(diagnostic_only=True,operator_qualified=False,candidate_commit='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3',
        change='only compiler memory clobbers around existing CQ DCCI; no algorithm or counter path changes',
        baseline_header_sha256=HEADER_SHA,overlay_header_sha256=sha(overlay),baseline_object_sha256=sha(binary),commands=[compile_cmd,link_cmd])
    started=time.monotonic();codes=[]
    for index,command in enumerate((compile_cmd,link_cmd)):
        with (dest/('build-%d.log'%index)).open('wb') as log:
            code=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=200).returncode
        codes.append(code)
        if code:break
    report.update(exit_codes=codes,build_wall_seconds=time.monotonic()-started)
    if codes==[0,0]:
        a,b=text_section(binary),text_section(linked)
        report.update(text_identical=a==b,baseline_text_sha256=hashlib.sha256(a).hexdigest(),
            overlay_text_sha256=hashlib.sha256(b).hexdigest(),overlay_object_sha256=sha(linked),
            overlay_object_path=str(linked),baseline_text_bytes=len(a),overlay_text_bytes=len(b))
        write(dest/'report.json',report)
        report['saved_timeout_observations']=postmortem()
    assert sha(header)==HEADER_SHA,'read_only_candidate_header_changed'
    write(dest/'report.json',report)
    summary={k:v for k,v in report.items() if k!='commands'}
    print(json.dumps(summary,separators=(',',':')),flush=True)
    return 0 if codes==[0,0] else 1

if __name__=='__main__':raise SystemExit(main())
