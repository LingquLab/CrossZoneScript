"""Verify the accepted build and runtime support before one bounded device phase."""
from pathlib import Path,PurePosixPath
import hashlib,json,os,stat,subprocess,sys,zipfile
ROOT=Path('/root/tilelang-107-targeted-p2-20261117-run01')
sys.path.insert(0,str(ROOT))
from operations import integrity,gate,require,save,sha
BUILD_SHA='e7c80dc768fe044aa99b11a555fdf2ed13d415899d56a617b76129367bbe295a'
RUNTIME_SHA='0701652dd37426a2bddf2a8b9059118e59a1221e9f7adbc001f0d39602d9c232'
VARIANTS=('B','U','R64','R128')

def prerequisite():
    integrity()
    require(len(BUILD_SHA)==64 and len(RUNTIME_SHA)==64,'Build/runtime SHA must be frozen first')
    frozen=json.loads((ROOT/'experiment.json').read_text())
    require(sha(ROOT/'build-summary.json')==BUILD_SHA,'Build receipt changed')
    b=json.loads((ROOT/'build-summary.json').read_text())
    require(b['status']=='PASS' and b['candidate']==frozen['candidate'] and b['experiment_revision']==frozen['experiment_revision'] and b['variants']==list(VARIANTS) and b['cores']==[64,64] and b['host_abi_bytes']==112,'Unverified/wrong candidate build')
    for name,digest in b['compiled_artifacts'].items():require(sha(ROOT/name)==digest,'Built artifact changed')
    return b

def phase_ports(phase):
    require(phase in ('correctness','capture'),'Unknown device phase')
    indices=range(52) if phase=='correctness' else range(12,52)
    return [p for i in indices for p in (*range(20000+64*i,20032+64*i),*range(31000+3*i,31003+3*i))]

def compact_wire(result):
    # Full parent/group receipts stay remote; green returns only bounded summary.
    wire={k:v for k,v in result.items() if k!='parent'}
    if 'parent' in result:
        wire['parent']={k:v for k,v in result['parent'].items() if k!='groups'}
    wire['groups_in_remote_parent']=len(result.get('parent',{}).get('groups',[]))
    require(len(json.dumps(wire,ensure_ascii=True,separators=(',',':')).encode())<14000,'Compact phase summary exceeds transport budget')
    return wire

def main():
    phase=sys.argv[1];require(phase in ('prepare','correctness','capture'),'Unknown phase')
    build=prerequisite()
    if phase=='prepare':
        require(not any((ROOT/p).exists() for p in ('correctness','correctness-launch','capture','capture-launch')),'Saved device evidence; audit before retry')
        (ROOT/'runtime-support01').mkdir(exist_ok=False)
        print(json.dumps({'status':'PREPARED','npu_launch':False}));return 0
    if phase=='correctness':
        archive=ROOT/'runtime-support01/runtime.zip'
        require(sha(archive)==RUNTIME_SHA,'Runtime transfer changed')
        with zipfile.ZipFile(archive) as z:
            names=set(z.namelist());manifest=json.loads(z.read('runtime-manifest.json'))
            require(names=={'controller.py','harness.py','phase_parent.py','runtime-manifest.json'} and len(z.infolist())==4 and z.testzip() is None,'Runtime archive structure')
            for info in z.infolist():require(info.file_size<100000 and not stat.S_ISLNK(info.external_attr>>16),'Unsafe runtime member')
            for name,digest in manifest.items():
                raw=(ROOT/name).read_bytes() if name=='operations.py' else z.read(name)
                require(hashlib.sha256(raw).hexdigest()==digest,'Runtime source SHA')
            for name in sorted(names):
                target=ROOT/name;require(not target.exists(),'Runtime evidence exists; no overwrite');target.write_bytes(z.read(name))
    manifest=json.loads((ROOT/'runtime-manifest.json').read_text())
    for name,digest in manifest.items():require(sha(ROOT/name)==digest,'Runtime harness changed')
    require(not any((ROOT/p).exists() for p in (phase,phase+'-launch')),'Saved phase exists; inspect without replay')
    if phase=='capture':
        prior=json.loads((ROOT/'correctness-launch/summary.json').read_text());require(prior['status']=='PASS' and len(prior['groups'])==12,'Twelve fresh correctness groups prerequisite')
    directory=ROOT/(phase+'-entry01');directory.mkdir(exist_ok=False)
    result={'schema':'tilelang_targeted_p2_107_'+phase+'.v1','status':'FAIL_OR_UNVERIFIED','candidate':build['candidate'],'experiment_revision':build['experiment_revision'],'variants':list(VARIANTS),'phase':phase,'devices':[0,1],'cores':[64,64],'build_summary_sha256':BUILD_SHA,'runtime_support_sha256':RUNTIME_SHA,'clock':'NOT_VERIFIED','npu_launch':False}
    try:
        ports=phase_ports(phase)
        initial=gate(directory/'initial-gate',ports=ports)
        result['initial_gate']={k:initial[k] for k in ('status','utc','ports_checked','snapshot_only')}
        with (directory/'parent.stdout.log').open('xb') as out,(directory/'parent.stderr.log').open('xb') as err:
            process=subprocess.run(['/usr/bin/python3',str(ROOT/'phase_parent.py'),phase],stdin=subprocess.DEVNULL,stdout=out,stderr=err,timeout=4050 if phase=='correctness' else 13350)
        save(directory/'parent.receipt.json',{'exit_code':process.returncode})
        p=ROOT/(phase+'-launch/summary.json');parent=json.loads(p.read_text())
        result.update(parent=parent,parent_summary_sha256=sha(p),npu_launch=parent.get('npu_launch'))
        integrity()
        count=12 if phase=='correctness' else 40
        require(process.returncode==0 and parent['status']=='PASS' and len(parent['groups'])==count,'Phase acceptance failed')
        result.update(status='PASS',warmup_per_group=20,measured_per_group=1,fresh_rank_groups=count,rank_receipts=count*2,allreduce_values_checked=count*44,rank_captures=80 if phase=='capture' else 0,original_configuration_preserved=True,source_root=str(ROOT/phase))
    except Exception as e:
        result.update(error_type=type(e).__name__,error_detail=str(e)[:240])
        if any((ROOT/phase).glob('g*/launch-intent.json')):result['npu_launch']=None
    save(directory/'summary.json',result)
    wire=compact_wire(result)
    text=json.dumps(wire,ensure_ascii=True,separators=(',',':'))
    save(directory/'wire-summary.json',wire)
    print(text)
    return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
