"""Serial, first-error-stop 3+10 fixed-P2 groups in the private 107 mount view."""
from pathlib import Path
from types import SimpleNamespace
import datetime,json,os,shutil,struct,sys,time,zipfile
from operations import ROOT,CANN,gate,integrity,require,save,sha
CANDIDATE='083ce35ee3847f11a3c42222b05167275ffecdbd'
REV='a4b8257a44f78849cf2d2db37cccffee9fb295e4'

def build_integrity():
    integrity(private=True)
    b=json.loads((ROOT/'build-summary.json').read_text())
    require(b['status']=='PASS' and b['candidate']==CANDIDATE and b['experiment_revision']==REV and b['cores']==[64,64] and b['host_abi_bytes']==112,'Independent build prerequisite')
    for name,digest in b['compiled_artifacts'].items():require(sha(ROOT/name)==digest,'Built artifact changed')
    require(sha(ROOT/'payload.zip')==b['bundle_sha256'],'Frozen payload changed')
    with zipfile.ZipFile(ROOT/'payload.zip') as z:
        manifest=json.loads(z.read('bundle-manifest.json'))
        for name,digest in manifest.items():
            if name.startswith('source/'):
                target=ROOT/name
            elif name.startswith(('fixture/','generated/')) or name=='input-identity.json':target=ROOT/name
            else:continue
            require(sha(target)==digest,'Frozen experiment input changed: '+name)
    require(sha(ROOT/'payload/source-manifest.json')==b['source_manifest_sha256'],'Source manifest changed')
    require(sha(ROOT/'artifact-hashes.json')==b['artifact_manifest_sha256'],'Artifact manifest changed')
    for name,digest in json.loads((ROOT/'runtime-manifest.json').read_text()).items():require(sha(ROOT/name)==digest,'Runtime harness changed')
    return b

def validate_native(directory,profile):
    from tilelang_dispatch.analyze_native import analyze
    a=analyze(directory/'native',ROOT/'fixture',ROOT/'generated')
    require(a['status']=='PASS' and a['rounds']==1 and a['warmup']==20 and a['mode']==('capture' if profile else 'correctness') and len(a['rank_results'])==2,'Full native analysis')
    rows=[];evidence={}
    for rank in (0,1):
        root=directory/'native/round1'/f'rank{rank}';log_path=root.parent/f'rank{rank}.log';log=log_path.read_text(errors='replace')
        markers=['TL950_HOST_LIFECYCLE HCCL_READY mode=4','TL950_HOST_LIFECYCLE TILEXR_READY','TL950_HOST_LIFECYCLE TILEXR_CLOSED','TL950_HOST_LIFECYCLE HCCL_CLOSED']
        positions=[log.index(m) for m in markers]
        require(all(log.count(m)==1 for m in markers) and positions==sorted(positions),'HCCL-first lifecycle mismatch')
        require(log.count('TileXR UDMA attaching to existing RA initialization')==1 and 'InitUDMA success' in log,'RA attachment missing')
        require(struct.unpack('<22i',(root/'allreduce-results.bin').read_bytes())==(2,)*22,'AllReduce SUM2 values')
        s=json.loads((root/'summary.json').read_text());w=json.loads((root/'warmup-summary.json').read_text())
        require(s['operator_correctness']==s['flags_cleared']=='PASS' and s['cores']==64 and s['verified_iterations']==21 and w['validated_outputs']==20,'21 Dispatch iterations/flags')
        if profile:
            r=a['rank_results'][rank];require(r['cores']==64 and len(r['captures'])==1,'Capture rank/core binding')
            c=r['captures'][0]
            require(c['capture_structure']=='PASS' and c['operator_correctness']=='PASS' and c['performance_sample_accepted'] is True and len(c['lanes'])==64 and c['unit']=='ticks' and c['cycles_per_us'] is None,'Capture acceptance/clock scope')
            require('F9.payload_clear' in c['stages'] and 'F8' in c['functions'],'Required marker coverage')
            for name in ('round20.dispatch.records','round20.dispatch.diagnostics.bin','timing.csv','allreduce.csv','host-timeline.csv','native-barriers.csv','native-schedule.json'):
                p=root/name;evidence[str(p.relative_to(directory))]=sha(p)
            csvs=list((directory/'native/round1'/f'prof-rank-{rank}').rglob('op_summary*.csv'));require(len(csvs)==1,'Profiler evidence count')
            evidence[str(csvs[0].relative_to(directory))]=sha(csvs[0])
        rows.append({'rank':rank,'host_lifecycle_verified':True,'dispatch_iterations_checked':21,'allreduce_values_checked':22,'log_sha256':sha(log_path)})
    exits=json.loads((directory/'native/round1/process-exits.json').read_text())
    require(len(exits)==2 and all(r['exit_code']==0 and not r['group_remaining'] for r in exits),'Own rank process cleanup')
    save(directory/'analysis.json',a);save(directory/'rank-validation.json',rows)
    if profile:
        evidence['analysis.json']=sha(directory/'analysis.json');evidence['native/run.json']=sha(directory/'native/run.json')
        save(directory/'capture-evidence.json',evidence)
    return rows

def one_group(phase,index,directory):
    directory.mkdir(exist_ok=False);base=28000+64*index;host=30000+3*index
    profile=phase=='capture'
    result={'index':index,'phase':phase,'status':'FAIL_OR_UNVERIFIED','npu_launch':False,'devices':[0,1],'cores':[64,64],'hccl_base_port':base,'host_base_port':host,'clock':'NOT_VERIFIED'}
    try:
        build_integrity()
        result['before']=gate(directory/'before',ports=[*range(base,base+32),host,host+1,host+2])
        require(not any(os.environ.get(k) for k in ('ASCEND_VISIBLE_DEVICES','ASCEND_RT_VISIBLE_DEVICES','LD_PRELOAD')) and os.environ.get('TILEXR_UDMA_ATTACH_EXISTING_RA')=='1','Unexpected launch environment')
        os.environ['HCCL_IF_BASE_PORT']=str(base)
        sys.path.insert(0,str(ROOT/'source/examples'))
        from harness import run as native_run
        args=SimpleNamespace(install=ROOT/'install',fixture=ROOT/'fixture',output=directory/'native',devices=[0,1],cores=[64,64],comm_port=host+1,barrier_port=host,mode=phase,rounds=1,warmup=20,timeout=240,pre_allreduce='allreduce')
        save(directory/'launch-intent.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'STARTING_FRESH_P2_GROUP','index':index,'profile':profile,'ports':[host,host+1,host+2]})
        result['npu_launch']=None
        execution=native_run(args);require(execution['status']=='PASS','Native group execution')
        rows=validate_native(directory,profile)
        # One post-group snapshot: busy/unknown stops; no retry or next launch.
        result['after']=gate(directory/'after')
        build_integrity()
        result.update(status='PASS',npu_launch=True,ranks=rows,analysis_sha256=sha(directory/'analysis.json'))
        if profile:result['capture_evidence_sha256']=sha(directory/'capture-evidence.json')
    except Exception as e:
        result.update(error_type=type(e).__name__,error_detail=str(e)[:240])
    finally:
        save(directory/'summary.json',result)
    require(result['status']=='PASS','First failed/unknown group: '+directory.name)
    return {'index':index,'status':'PASS','summary_sha256':sha(directory/'summary.json'),'analysis_sha256':result['analysis_sha256'],**({'capture_evidence_sha256':result['capture_evidence_sha256']} if profile else {})}

def main():
    phase=sys.argv[1];require(phase in ('correctness','capture'),'Phase outside plan')
    directory=ROOT/phase;directory.mkdir(exist_ok=False)
    result={'schema':'tilelang_fixedp2_107_phase.v1','candidate':CANDIDATE,'experiment_revision':REV,'phase':phase,'status':'FAIL_OR_UNVERIFIED','groups':[],'npu_launch':False,'clock':'NOT_VERIFIED'}
    try:
        build_integrity()
        if phase=='capture':
            p=ROOT/'correctness-launch/summary.json';prior=json.loads(p.read_text())
            require(prior['status']=='PASS' and len(prior['groups'])==3 and not prior['own_residual_children'],'Three fresh correctness groups prerequisite')
            for g in prior['groups']:require(sha(ROOT/'correctness'/('g'+str(g['index']).zfill(3))/'summary.json')==g['summary_sha256'],'Correctness evidence changed')
            profiler=Path(shutil.which('msprof') or '').resolve();require(profiler.is_file() and profiler.is_relative_to(CANN.resolve()),'Pinned msprof required')
            result['profiler_identity']={'path':str(profiler),'sha256':sha(profiler)}
        deadline=time.monotonic()+(950 if phase=='correctness' else 3300)
        for index in (range(3) if phase=='correctness' else range(3,13)):
            require(time.monotonic()<deadline,'Phase budget exhausted')
            if phase=='capture':require(sha(profiler)==result['profiler_identity']['sha256'],'Profiler changed')
            result['current_group']=index;save(directory/'progress.json',result)
            g=directory/('g'+str(index).zfill(3))
            try:row=one_group(phase,index,g)
            finally:
                if (g/'launch-intent.json').exists():result['npu_launch']=None
            result['npu_launch']=True;result['groups'].append(row);save(directory/'progress.json',result)
        result.pop('current_group',None);result['status']='PASS'
    except Exception as e:result.update(error_type=type(e).__name__,error_detail=str(e)[:250])
    save(directory/'summary.json',result)
    print(json.dumps(result,separators=(',',':')))
    return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
