"""Verify the accepted build and runtime support before one bounded device phase."""
from pathlib import Path,PurePosixPath
import hashlib,json,os,stat,subprocess,sys,zipfile
ROOT=Path('/root/tilelang-107-fixedp2-083ce35e-20261117-run01')
sys.path.insert(0,str(ROOT))
from operations import integrity,gate,require,save,sha
BUILD_SHA='a79c6075cbe9531c203d2548e919e66aa578f2a41e7ee5650d845e8711afee27'
RUNTIME_SHA='3a57efb49b8831daac50e4ad9fb08f92c9a8651026e62e6827726927d7978530'
REV='a4b8257a44f78849cf2d2db37cccffee9fb295e4'

def prerequisite():
    integrity()
    require(sha(ROOT/'build-summary.json')==BUILD_SHA,'Build receipt changed')
    b=json.loads((ROOT/'build-summary.json').read_text())
    require(b['status']=='PASS' and b['candidate']=='083ce35ee3847f11a3c42222b05167275ffecdbd' and b['experiment_revision']==REV and b['cores']==[64,64] and b['host_abi_bytes']==112,'Unverified/wrong candidate build')
    for name,digest in b['compiled_artifacts'].items():require(sha(ROOT/name)==digest,'Built artifact changed')
    return b

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
        prior=json.loads((ROOT/'correctness-launch/summary.json').read_text());require(prior['status']=='PASS' and len(prior['groups'])==3,'Three fresh correctness groups prerequisite')
    directory=ROOT/(phase+'-entry01');directory.mkdir(exist_ok=False)
    result={'schema':'tilelang_fixedp2_107_'+phase+'.v1','status':'FAIL_OR_UNVERIFIED','candidate':build['candidate'],'experiment_revision':REV,'phase':phase,'devices':[0,1],'cores':[64,64],'build_summary_sha256':BUILD_SHA,'runtime_support_sha256':RUNTIME_SHA,'clock':'NOT_VERIFIED','npu_launch':False}
    try:
        indices=range(13) if phase=='correctness' else range(3,13)
        ports=[p for i in indices for p in (*range(28000+64*i,28032+64*i),*range(30000+3*i,30003+3*i))]
        initial=gate(directory/'initial-gate',ports=ports)
        result['initial_gate']={k:initial[k] for k in ('status','utc','ports_checked','snapshot_only')}
        with (directory/'parent.stdout.log').open('xb') as out,(directory/'parent.stderr.log').open('xb') as err:
            process=subprocess.run(['/usr/bin/python3',str(ROOT/'phase_parent.py'),phase],stdin=subprocess.DEVNULL,stdout=out,stderr=err,timeout=1050 if phase=='correctness' else 3450)
        save(directory/'parent.receipt.json',{'exit_code':process.returncode})
        p=ROOT/(phase+'-launch/summary.json');parent=json.loads(p.read_text())
        result.update(parent=parent,parent_summary_sha256=sha(p),npu_launch=parent.get('npu_launch'))
        integrity()
        count=3 if phase=='correctness' else 10
        require(process.returncode==0 and parent['status']=='PASS' and len(parent['groups'])==count,'Phase acceptance failed')
        result.update(status='PASS',warmup_per_group=20,measured_per_group=1,fresh_rank_groups=count,rank_receipts=count*2,allreduce_values_checked=count*44,rank_captures=20 if phase=='capture' else 0,original_configuration_preserved=True,source_root=str(ROOT/phase))
    except Exception as e:
        result.update(error_type=type(e).__name__,error_detail=str(e)[:240])
        if any((ROOT/phase).glob('g*/launch-intent.json')):result['npu_launch']=None
    save(directory/'summary.json',result)
    print(json.dumps(result,ensure_ascii=True,separators=(',',':')))
    return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
