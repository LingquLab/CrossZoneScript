# Copyright (c) 2026 TileXR Project.
"""Prepare fixtures and run isolated, sequential SST phases with checked evidence."""
import hashlib,json,re,shutil,struct,subprocess,time,fcntl
from pathlib import Path
ROOT=Path('/mnt/share/tilexr-sim-jobs/fac4ae28-1586-42fe-b55b-ff09982f17ec/run-40596e2-a1')
TEMPLATE=Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549')
REVISION='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3'
CHECKER='source/tools/unitest/simulator/experiments/inter_server_send/fixture.py'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def checked_container():
    identity=json.loads((ROOT/'identity.json').read_text());cid=identity['container_id']
    info=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
    assert info['Id']==cid and info['Image']==identity['image'] and info['HostConfig']['NetworkMode']=='none'
    assert info['Config']['Labels']['crosszone.session']=='fac4ae28-1586-42fe-b55b-ff09982f17ec'
    assert any(m['Source']==str(ROOT) and m['Destination']=='/work' for m in info['Mounts'])
    return cid,info

def execute(cid,argv,log,seconds=500):
    cmd=['docker','exec',cid,'bash','-c','set -e; source /opt/sim/cann-env.sh; export PATH=/opt/sim/Ascend/cann-9.1.0/tools/bisheng_compiler/bin:/opt/sim/Ascend/cann-9.1.0/aarch64-linux/ccec_compiler/bin:$PATH; exec "$@"','bash']+argv
    with Path(log).open('wb') as f:p=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=seconds)
    return p.returncode

def platform(case,tools,checker,entry):
    template=json.loads((TEMPLATE/'case-manifest.json').read_text())
    assert json.loads((TEMPLATE/'run.json').read_text())['status']=='sst_and_checker_passed'
    for name,digest in template['files'].items():
        rel=Path(name);assert not rel.is_absolute() and '..' not in rel.parts
        if rel.parts[0] in ('input_bin','fixture-data','kernel') or name in ('workload.yml','export-layout.json',
            'case-manifest.json','loader-manifest.json','blue-build-record.json','runtime-layout.json','blue-case-identity.json'):continue
        src,dst=TEMPLATE/rel,case/rel;assert sha(src)==digest;dst.parent.mkdir(parents=True,exist_ok=True)
        if name==CHECKER:shutil.copyfile(tools/checker,dst)
        elif name=='adapter/exports.py':shutil.copyfile(tools/'exports.py',dst)
        elif name=='adapter/run.py':
            s=src.read_text();needle='manifest["entry"] != "tilexr_ep_ht_inter_server_send_kernel"';assert s.count(needle)==1
            s=s.replace(needle,'manifest["entry"] != '+repr(entry))
            assert s.count('            child = subprocess.Popen(')==1
            s=s.replace('import subprocess\n','import subprocess\nimport time\n')
            s=s.replace('            child = subprocess.Popen(',
                '            record["sst_started_unix"] = time.time()\n            sst_wall_start = time.monotonic()\n            child = subprocess.Popen(')
            needle_final='        finally:\n            signal.signal(signal.SIGTERM, previous)'
            assert s.count(needle_final)==1
            s=s.replace(needle_final,'        finally:\n            record["sst_wall_seconds"] = time.monotonic() - sst_wall_start\n            record["sst_finished_unix"] = time.time()\n            record_path.write_text(json.dumps(record, indent=2) + "\\n")\n            signal.signal(signal.SIGTERM, previous)')
            dst.write_text(s)
        elif name=='v310_config/workload_config.yml':continue
        else:shutil.copyfile(src,dst)
    shutil.copyfile(case/'workload.yml',case/'v310_config/workload_config.yml')
    meta=json.loads((case/'fixture-data/manifest.json').read_text());fixture=case/'fixture-data'
    manifest=dict(template,entry=entry,candidate_commit=REVISION,kernel_sha256=sha(case/'kernel/inter_server_send.o'),
        checker_sha256=sha(case/CHECKER),variant=case.name)
    manifest['fixture_files']={str(p.relative_to(fixture)):sha(p) for p in fixture.rglob('*') if p.is_file()}
    manifest['files']={str(p.relative_to(case)):sha(p) for p in case.rglob('*') if p.is_file() and p.name!='case-manifest.json'}
    write(case/'case-manifest.json',manifest)
    return meta

def run_case(cid,case,tools,checker,entry,seconds=900):
    meta=platform(case,tools,checker,entry);runtime='/work/'+str(case.relative_to(ROOT))
    write(ROOT/'progress.json',{'stage':'sst','case':str(case),'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'updated_unix':time.time()})
    started=time.time();mono=time.monotonic()
    code=execute(cid,['python3',runtime+'/adapter/run.py','--case',runtime,'--fixture',runtime+'/fixture-data',
        '--checker',runtime+'/'+CHECKER,'--environment',runtime+'/sst-environment.json','--threads','8','--timeout',str(seconds)],case/'runner.log',seconds+130)
    result={'case':str(case),'candidate_commit':REVISION,'world':64,'tokens':meta['tokens'],'hidden':128,'topk':6,'experts':256,
        'dtype':'fp32','phase':meta.get('phase',meta.get('stage','pipeline')),'runner_exit':code,
        'compiler_scalar_dcci':meta.get('compiler_scalar_dcci',False),'pipeline_build_variant':meta.get('pipeline_build_variant'),
        'stage_isolation_noinline':meta.get('stage_isolation_noinline',False),
        'barrier_algorithm':meta.get('barrier_algorithm'),
        'extra_round_fence':meta.get('extra_round_fence'),
        'kernel_compatibility_variant':meta.get('kernel_compatibility_variant','original'),
        'compatibility_header_sha256':meta.get('compatibility_header_sha256'),
        'compatibility_baseline_header_sha256':meta.get('compatibility_baseline_header_sha256'),
        'compatibility_inter_source_sha256':meta.get('compatibility_inter_source_sha256'),
        'compatibility_api_header_sha256':meta.get('compatibility_api_header_sha256'),
        'wall_seconds':time.monotonic()-mono,'started_unix':started,'finished_unix':time.time(),
        'kernel_sha256':sha(case/'kernel/inter_server_send.o'),'routes_sha256':meta['routes_sha256'],
        'platform_simulated_us':None,'profiling_operator_us':None}
    if (case/'run.json').is_file():
        r=json.loads((case/'run.json').read_text());result['run']=r
        result['sst_wall_seconds']=r.get('sst_wall_seconds')
        result['wall_scope']='runner_including_SST_integrity_export_checker'
        result['sst_wall_scope']='SST_Popen_to_wait_exit' if r.get('sst_wall_seconds') is not None else 'not_separately_instrumented'
    if (case/'check.log').is_file():
        text=(case/'check.log').read_text(errors='replace').strip()
        try:result['checked']=json.loads(text)
        except ValueError:pass
    if (case/'sim.log').is_file():
        with (case/'sim.log').open('rb') as f:f.seek(max(0,(case/'sim.log').stat().st_size-65536));tail=f.read().decode(errors='replace')
        match=re.search(r'Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)',tail)
        if match and result.get('run',{}).get('status')=='sst_and_checker_passed':
            result['platform_simulated_us']=float(match[1])*{'ns':.001,'us':1,'ms':1000,'s':1000000}[match[2]]
        result['model_errors']=[l[:300] for l in tail.splitlines() if re.search('fatal|segmentation fault|assertion|panic',l,re.I)][-4:]
        result['last_log_lines']=[l[:220] for l in tail.splitlines()[-4:]]
    for filename in ('check.log','runner.log'):
        p=case/filename
        if p.is_file():
            errors=[l[:300] for l in p.read_text(errors='replace').splitlines() if re.search('Error:|error:|mismatch|not found|No such file',l)]
            if errors:result[filename+'_errors']=errors[-3:]
    result['numerical_qualified']=code==0 and result.get('run',{}).get('status')=='sst_and_checker_passed' and 'checked' in result
    if result['numerical_qualified']:
        program="import sys,json;sys.path.insert(0,"+repr('/work/'+tools.name)+");import profile;result=profile.summarize("+repr(runtime)+","+str(meta.get('profiling_capacity',8192))+","+str(meta.get('blocks',16))+")"
        if meta.get('joint_single_invocation') and meta.get('local_profile_expected',True):
            program+=";result['local_details']=profile.summarize("+repr(runtime)+",8192,16,'local_profiling')"
        program+=";print(json.dumps(result))"
        profile_code=execute(cid,['python3','-c',program],case/'profiling-check.log',120)
        if profile_code==0:
            result['profiling']=json.loads((case/'profiling-check.log').read_text())
            result['profiling_operator_us']=result['profiling'].get('operator_us')
        else:result['profiling_error']='see_profiling-check.log'
    result['qualified']=result['numerical_qualified'] and 'profiling' in result
    result['diagnostic_only']=meta.get('diagnostic_only',False)
    result['operator_qualified']=result['qualified'] and not result['diagnostic_only']
    result['run']={k:v for k,v in result.get('run',{}).items() if k in ('status','returncode','guards','error','started_unix','finished_unix','elapsed_seconds')}
    write(case/'phase-result.json',result);write(ROOT/'progress.json',dict(stage='finished',case=str(case),qualified=result['qualified'],updated_unix=time.time()))
    if result.get('kernel_compatibility_variant') in ('sq-align512','sq-align-all512','sq-align-all512-cq-barrier'):
        compact={k:result.get(k) for k in ('case','qualified','operator_qualified','run','platform_simulated_us','profiling_operator_us','sst_wall_seconds','wall_seconds','model_errors','kernel_sha256','kernel_compatibility_variant','compatibility_inter_source_sha256','compatibility_api_header_sha256')}
        compact['profiling_lanes']=result.get('profiling',{}).get('committed_lanes')
        compact['profiling_dropped']=result.get('profiling',{}).get('dropped_records')
        print(json.dumps(compact,separators=(',',':')),flush=True)
    else:print(json.dumps(result,separators=(',',':')),flush=True)
    return 0 if result['qualified'] else 1

def local(cid,tools,tokens,phase,attempt="a1",kernel_variant='original'):
    assert attempt in ("a1","a2","a3")
    assert phase in ('send','reduce','fused')
    assert kernel_variant in ('original','cq-memory-barrier','sq-align512')
    compatibility=None
    if kernel_variant=='cq-memory-barrier':
        assert (tokens,phase,attempt)==(16,'send','a2')
        compatibility=json.loads((ROOT/'cq-compiler-barrier-a2/report.json').read_text())
        assert compatibility['exit_codes']==[0,0] and compatibility['text_identical'] is False
        assert sha(ROOT/'source/include/tilexr/comm/udma/device/api.h')==compatibility['baseline_header_sha256']
        assert sha(ROOT/'cq-compiler-barrier-a2/include/tilexr/comm/udma/device/api.h')==compatibility['overlay_header_sha256']
    if kernel_variant=='sq-align512':
        assert (tokens,phase,attempt) in ((16,'send','a3'),(32,'send','a2'),(4,'send','a3'))
        compatibility=json.loads((ROOT/'sq-align512-a1/report.json').read_text())
        assert compatibility['exit_codes']==[0,0]
        assert sha(ROOT/'source/src/moonep/combine_ht/kernels/combine_ht_kernel.h')==compatibility['baseline_header_sha256']
        assert sha(ROOT/'sq-align512-a1/include/combine_ht_kernel.h')==compatibility['overlay_header_sha256']
    asset=ROOT/'assets'/('64p-t%d'%tokens)
    identity=json.loads((asset/'identity.json').read_text())
    case=ROOT/'cases'/('64p-t%d-local-%s-%s'%(tokens,phase,attempt));case.mkdir(parents=True,exist_ok=False)
    for name in ('input_bin','kernel'):shutil.copytree(asset/'loader'/name,case/name)
    shutil.copytree(asset/'fixture-data',case/'fixture-data');shutil.copyfile(asset/'routes.json',case/'routes.json')
    layout=json.loads((asset/'loader/export-layout.json').read_text());layout['required_exports']=['returned','completed','status','output','profiling']
    write(case/'export-layout.json',layout)
    runtime='/work/cases/'+case.name
    workload=json.loads((asset/'loader/workload.yml').read_text().replace(identity['runtime_root'],runtime))
    provenance=hashlib.sha256()
    for row in layout['ranks']:
        folder=case/'input_bin'/('rank%02d'%row['rank']);args=bytearray((folder/'kernel_args.bin').read_bytes())
        struct.pack_into('<I',args,156,{'send':1,'reduce':2,'fused':3}[phase]);(folder/'kernel_args.bin').write_bytes(args)
        workload['workload_config']['chip%d'%row['rank']]['kernel_param']['word19']['value']=struct.unpack_from('<Q',args,152)[0]
        if phase=='reduce':
            ws=bytearray((folder/'workspace.bin').read_bytes())
            for field in ('returned','completed','output'):
                bounds=row['exports'][field]
                filename='initial-output.bin' if field=='output' else 'golden-'+field+'.bin'
                payload=(asset/'fixture-data'/folder.name/filename).read_bytes()
                assert len(payload)==bounds['size'];offset=bounds['addr']-struct.unpack_from('<Q',args,0)[0]
                ws[offset:offset+len(payload)]=payload;provenance.update(hashlib.sha256(payload).digest())
            (folder/'workspace.bin').write_bytes(ws)
    if compatibility is not None:
        binary=ROOT/('sq-align512-a1/local_ht.o' if kernel_variant=='sq-align512' else 'cq-compiler-barrier-a2/local_ht.o')
        assert sha(binary)==compatibility['object_sha256' if kernel_variant=='sq-align512' else 'overlay_object_sha256']
        raw=binary.read_bytes();(case/'kernel/inter_server_send.o').write_bytes(raw+bytes(10485760-len(raw)))
    elif (ROOT/'profile-build/result.json').is_file():
        instrument=json.loads((ROOT/'profile-build/result.json').read_text())['targets']['local_ht']
        binary=ROOT/instrument['path'];assert sha(binary)==instrument['sha256']
        raw=binary.read_bytes();(case/'kernel/inter_server_send.o').write_bytes(raw+bytes(10485760-len(raw)))
    write(case/'workload.yml',workload)
    meta=json.loads((case/'fixture-data/manifest.json').read_text());meta.update(kernel_sha256=sha(case/'kernel/inter_server_send.o'),profiling_tail_flush=(ROOT/'profile-build/result.json').is_file(),phase=phase,
        input_mode='independent_known_inputs' if phase=='reduce' else 'expert_outputs_and_routes',
        consumed_actual_sha256=None,consumed_input_sha256=provenance.hexdigest() if phase=='reduce' else None)
    meta['kernel_compatibility_variant']=kernel_variant
    if compatibility is not None:
        meta.update(compatibility_header_sha256=compatibility['overlay_header_sha256'],
            compatibility_baseline_header_sha256=compatibility['baseline_header_sha256'],
            compatibility_scope=('40596e2 plus SIM_SEND SQ publication split/drain at 512B boundaries' if kernel_variant=='sq-align512' else '40596e2 plus two compiler memory clobbers around existing DCCI; original source mount unchanged'))
    write(case/'fixture-data/manifest.json',meta)
    seconds=900 if compatibility is not None else {4:900,16:1800,32:2700,64:3600,128:5400}[tokens]
    return run_case(cid,case,tools,'check_local.py','tilexr_moonep_combine_ht_kernel',seconds)

def prepare_asset(cid,tools,tokens):
    runtime='/work/'+tools.name
    asset=ROOT/'assets'/('64p-t%d'%tokens);asset.parent.mkdir(exist_ok=True)
    binary='/work/build-fp32/src/moonep/combine_ht/tilexr_moonep_combine_ht_kernel.o'
    code=execute(cid,['python3',runtime+'/fixture.py','--source','/work/source','--output','/work/assets/'+asset.name,
        '--kernel',binary,'--world','64','--tokens',str(tokens)],ROOT/('prepare-%d.log'%tokens),550)
    if code==0:
        print(json.dumps(json.loads((asset/'identity.json').read_text()),separators=(',',':')))
        abis=[]
        for rank in range(64):
            p=subprocess.run(['docker','exec',cid,str('/work/assets/'+asset.name+'/abi-probe'),str(rank),str(tokens),'128','6',str(64*tokens*6),str(64*tokens*6),'2026','-','64'],capture_output=True,timeout=10)
            assert p.returncode==0;abis.append(json.loads(p.stdout))
        write(asset/'send-abis.json',abis)
    else:print(json.dumps({'prepare_exit':code,'log':str(ROOT/('prepare-%d.log'%tokens))}))
    return code

def return_phase(cid,tools,tokens,stage,attempt="a1",kernel_variant='original'):
    runtime='/work/'+tools.name
    assert stage in ('return-independent','return','inter-send-independent','inter-send','final-independent','final-joint')
    code=execute(cid,['python3',runtime+'/return_fixture.py','--root','/work','--stage',stage,'--tokens',str(tokens),
        '--abis','/work/assets/64p-t%d/send-abis.json'%tokens,'--attempt',attempt],ROOT/('prepare-%d-%s-%s.log'%(tokens,stage,attempt)),500)
    if code:
        print(json.dumps({'prepare_exit':code,'stage':stage,'log':str(ROOT/('prepare-%d-%s.log'%(tokens,stage)))}));return code
    case=ROOT/'cases'/('64p-t%d-%s-%s'%(tokens,stage,attempt));meta=json.loads((case/'fixture-data/manifest.json').read_text())
    assert kernel_variant in ('original','sq-align-all512')
    if kernel_variant=='sq-align-all512':
        assert (tokens,stage,attempt) in ((16,'inter-send-independent','a2'),(32,'inter-send-independent','a2'))
        report=json.loads((ROOT/'inter-sq-align512-a1/report.json').read_text());assert report['exit_codes']==[0,0]
        binary=ROOT/'inter-sq-align512-a1/inter.o';assert sha(binary)==report['inter_object_sha256']
        raw=binary.read_bytes();(case/'kernel/inter_server_send.o').write_bytes(raw+bytes(10485760-len(raw)))
        meta.update(kernel_sha256=sha(case/'kernel/inter_server_send.o'),kernel_compatibility_variant=kernel_variant,compatibility_inter_source_sha256=report['overlay_inter_sha256'])
        write(case/'fixture-data/manifest.json',meta)
    return run_case(cid,case,tools,'check_return.py',meta['entry'],900 if kernel_variant=='sq-align-all512' else {4:1000,16:1800,32:2700,64:3600,128:5400}[tokens])

def pipeline_phase(cid,tools,tokens,request,seconds=None):
    runtime='/work/'+tools.name;attempt=request.get('attempt','a1')
    code=execute(cid,['python3',runtime+'/pipeline_fixture.py','--root','/work','--tools',runtime,'--tokens',str(tokens),
        '--abis','/work/assets/64p-t%d/send-abis.json'%tokens,'--attempt',attempt,'--build-variant',request.get('build_variant','original')],
        ROOT/('prepare-pipeline-%d-%s.log'%(tokens,attempt)),500)
    if code:
        print(json.dumps({'prepare_exit':code,'log':str(ROOT/('prepare-pipeline-%d-%s.log'%(tokens,attempt)))}));return code
    case=ROOT/'cases'/('64p-t%d-pipeline-%s'%(tokens,attempt))
    if seconds is None:seconds={4:2400,16:1800,32:2700,64:3600,128:5400}[tokens]
    return run_case(cid,case,tools,'check_pipeline.py','tilexr_ht_pipeline_sst',seconds)

def status(cid,info):
    result={'root':str(ROOT),'container_id':cid,'running':info['State']['Running'],'oom_killed':info['State']['OOMKilled']}
    if (ROOT/'progress.json').is_file():result['progress']=json.loads((ROOT/'progress.json').read_text())
    result['cases']=[]
    for case in sorted((ROOT/'cases').glob('*')) if (ROOT/'cases').is_dir() else []:
        if case.is_dir():
            row={'name':case.name}
            for n in ('phase-result.json','run.json'):
                if (case/n).is_file():row[n]=json.loads((case/n).read_text())
            result['cases'].append(row)
    if info['State']['Running']:
        p=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
        result['sst_processes']=[l.strip() for l in p.stdout.splitlines() if 'sstsim' in l]
    print(json.dumps(result,separators=(',',':')));return 0

def batch_status(cid,info,tokens,evidence=False):
    assert tokens in (4,16,32,64,128)
    result=dict(utc_unix=time.time(),tokens=tokens,container_running=info['State']['Running'],oom_killed=info['State']['OOMKilled'],cases=[])
    for case in sorted((ROOT/'cases').glob('64p-t%d-*'%tokens)):
        if not case.is_dir() or case.name.endswith('-preparation'):continue
        row={'name':case.name}
        if (case/'phase-result.json').is_file():
            full=json.loads((case/'phase-result.json').read_text())
            for key in ('phase','qualified','operator_qualified','numerical_qualified','platform_simulated_us',
                        'profiling_operator_us','sst_wall_seconds','wall_seconds','model_errors'):
                if key in full:row[key]=full[key]
            row['run_status']=full.get('run',{}).get('status')
            row['guards']=full.get('run',{}).get('guards')
            if evidence:row['phase_result']=full
        elif (case/'run.json').is_file():
            run=json.loads((case/'run.json').read_text());row['run_status']=run['status']
            if run.get('sst_started_unix'):row['sst_elapsed_seconds']=time.time()-run['sst_started_unix']
        else:row['run_status']='preparing_or_no_saved_run'
        if evidence and row['run_status']!='sst_and_checker_passed':
            for name in ('runner.log','check.log','profiling-check.log'):
                path=case/name
                if path.is_file():
                    with path.open('rb') as stream:
                        stream.seek(max(0,path.stat().st_size-8000));lines=stream.read().decode(errors='replace').splitlines()
                    row[name]=[line[:300] for line in lines[-5:]]
        elif evidence and not row.get('qualified'):
            path=case/'profiling-check.log'
            if path.is_file():row['profiling-check.log']=[line[:300] for line in path.read_text(errors='replace').splitlines()[-5:]]
        log=case/'sim.log'
        if log.is_file():
            row['sim_log_bytes']=log.stat().st_size
            with log.open('rb') as stream:
                stream.seek(max(0,log.stat().st_size-2048));tail=stream.read().decode(errors='replace')
            cycles=re.findall(r'Sim: ([0-9]+)',tail)
            if cycles:row['last_logged_sim_cycle']=int(cycles[-1])
        result['cases'].append(row)
    progress=ROOT/'progress.json'
    if progress.is_file():result['progress']=json.loads(progress.read_text())
    if info['State']['Running']:
        p=subprocess.run(['docker','top',cid,'-eo','pid,etimes,pcpu,comm'],capture_output=True,text=True,timeout=10)
        assert p.returncode==0
        result['sst_processes']=[line.strip() for line in p.stdout.splitlines() if 'sstsim' in line]
    print(json.dumps(result,separators=(',',':')));return 0

def main(request):
    cid,info=checked_container();action=request['action'];assert info['State']['Running'] or action in ('status','batch-status','stop')
    if action=='status':return status(cid,info)
    if action=='batch-status':return batch_status(cid,info,request['tokens'],request.get('evidence',False))
    with (ROOT/'execution.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if action=='stop':
            p=subprocess.run(['docker','stop','-t','15',cid],capture_output=True,timeout=40)
            stopped=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
            print(json.dumps({'stop_exit':p.returncode,'stopped':not stopped['State']['Running'],'oom_killed':stopped['State']['OOMKilled']}));return p.returncode
        bundle=hashlib.sha256(json.dumps(request['files'],sort_keys=True).encode()).hexdigest()[:12]
        tools=ROOT/('scripts-'+bundle);tools.mkdir(exist_ok=True)
        for name,text in request['files'].items():
            assert Path(name).name==name
            p=tools/name
            if p.exists():assert p.read_text()==text
            else:p.write_text(text)
        runtime='/work/'+tools.name
        tokens=request.get('tokens',4);assert tokens in (4,16,32,64,128)
        if tokens!=4 and action not in ('instrument','pipeline-build'):
            gates=list((ROOT/'cases').glob('64p-t4-pipeline-a*/phase-result.json'))
            gate_reports=[json.loads(p.read_text()) for p in gates]
            assert any(q.get('qualified') and q.get('operator_qualified') and q.get('candidate_commit')==REVISION for q in gate_reports),'bs4_joint_gate'
        if action=='dcci-pipeline':
            code=execute(cid,['python3',runtime+'/dcci_pipeline.py',runtime],ROOT/'pipeline-dcci-prepare.log',350)
            if code:
                print(json.dumps({'prepare_exit':code,'log':str(ROOT/'pipeline-dcci-prepare.log')}));return code
            return run_case(cid,ROOT/'cases/64p-t4-pipeline-dcci-diagnostic-a1',tools,'check_pipeline.py','tilexr_ht_pipeline_sst',1200)
        if action=='snapshot-pipeline':
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout,'another_sst_is_running'
            code=execute(cid,['python3',runtime+'/snapshot_pipeline.py'],ROOT/'pipeline-snapshot-runner.log',1800)
            report=ROOT/'cases/64p-t4-pipeline-snapshot200us-a1/diagnostic.json'
            if report.is_file():print(json.dumps(json.loads(report.read_text()),separators=(',',':')),flush=True)
            return code
        if action=='isolate-pipeline':
            variant=request.get('build_variant')
            assert tokens==4 and (request.get('attempt'),variant) in (('a3','isolated-inter'),('a4','leader-barrier'),('a5','four-module-joint'))
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout,'another_sst_is_running'
            code=execute(cid,['python3',runtime+'/isolate_inter_pipeline.py',runtime,variant],ROOT/('pipeline-'+variant+'-runner.log'),350)
            report=ROOT/('pipeline-'+variant+'-a1/build-result.json')
            if report.is_file():print(json.dumps(json.loads(report.read_text()),separators=(',',':')),flush=True)
            if code:return code
        if action=='cq-compiler-barrier':
            assert tokens==16 and request.get('attempt')=='a2'
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout,'another_sst_is_running'
            code=execute(cid,['python3',runtime+'/cq_compiler_barrier.py'],ROOT/'cq-compiler-barrier-a2-runner.log',500)
            report=ROOT/'cq-compiler-barrier-a2/report.json'
            if report.is_file():
                data=json.loads(report.read_text());data.pop('commands',None)
                print(json.dumps(data,separators=(',',':')),flush=True)
            return code
        if action=='cq-visible-joint16':
            assert tokens==16
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout
            code=execute(cid,['python3',runtime+'/cq_visible_joint_build.py',runtime],ROOT/'cq-visible-joint16-build.log',450)
            if code:return code
            return pipeline_phase(cid,tools,16,dict(attempt='a4',build_variant='four-module-sq-align-all512-cq'),1200)
        if action=='joint-cq-trace16':
            assert tokens==16
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout
            log=ROOT/'joint-cq-trace16-runner.log'
            code=execute(cid,['python3',runtime+'/joint_cq_trace.py'],log,1350)
            report=ROOT/'cases/64p-t16-pipeline-a4-cq-trace185us-a1/diagnostic.json'
            if report.is_file():
                print(json.dumps(json.loads(report.read_text()),separators=(',',':')),flush=True)
            return code
        if action=='joint-cq-followup16':
            assert tokens==16
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout
            log=ROOT/'joint-cq-followup16-runner.log'
            code=execute(cid,['python3',runtime+'/all_sq_align_snapshot.py','--cq-followup'],log,1450)
            report=ROOT/'cases/64p-t16-pipeline-a4-snapshot240us-a1/diagnostic.json'
            if report.is_file():
                summary=json.loads(report.read_text())
                summary.pop('pending_samples',None)
                print(json.dumps(summary,separators=(',',':')),flush=True)
            return code
        if action=='all-align-snapshot':
            assert tokens==16
            case=ROOT/'cases/64p-t16-pipeline-a3'
            prior=json.loads((case/'phase-result.json').read_text())
            assert not prior['qualified'] and prior['run']['status']=='timed_out'
            summary={k:prior.get(k) for k in ('case','run','sst_wall_seconds','wall_seconds','kernel_sha256','kernel_compatibility_variant')}
            log=case/'sim.log'
            if log.is_file():
                with log.open('rb') as f:f.seek(max(0,log.stat().st_size-65536));tail=f.read().decode(errors='replace')
                ticks=re.findall(r'Sim: ([0-9]+)[(]cycle[)]',tail)
                if ticks:summary['last_logged_sim_tick']=int(ticks[-1])
            print(json.dumps(dict(original_case=summary),separators=(',',':')),flush=True)
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout
            code=execute(cid,['python3',runtime+'/all_sq_align_snapshot.py'],ROOT/'all-align16-snapshot-runner.log',1500)
            report=ROOT/'cases/64p-t16-pipeline-all-sq-align-snapshot200us-a1/diagnostic.json'
            if report.is_file():
                summary=json.loads(report.read_text());summary.pop('pending_samples',None);summary.pop('dump_files',None);print(json.dumps(summary,separators=(',',':')),flush=True)
            return code
        if action=='inter-align-regression':
            assert tokens==16
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout
            code=execute(cid,['python3',runtime+'/inter_sq_align_build.py',runtime],ROOT/'inter-align512-build.log',800)
            if code:return code
            for n,attempt,seconds in ((16,'a3',1200),(32,'a2',1500)):
                code=return_phase(cid,tools,n,'inter-send-independent','a2','sq-align-all512')
                if code:return code
                code=pipeline_phase(cid,tools,n,dict(attempt=attempt,build_variant='four-module-sq-align-all512'),seconds)
                if code:return code
            return 0
        if action=='sq-align-snapshot':
            assert tokens==16
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout
            code=execute(cid,['python3',runtime+'/sq_align_snapshot.py'],ROOT/'sq-align16-snapshot-runner.log',1500)
            report=ROOT/'cases/64p-t16-pipeline-sq-align-snapshot200us-a1/diagnostic.json'
            if report.is_file():
                summary=json.loads(report.read_text());summary.pop('pending_samples',None);summary.pop('dump_files',None);print(json.dumps(summary,separators=(',',':')),flush=True)
            return code
        if action=='sq-align-regression':
            gate=json.loads((ROOT/'cases/64p-t16-local-send-a3/phase-result.json').read_text())
            assert gate['operator_qualified'] and gate['kernel_compatibility_variant']=='sq-align512'
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout
            code=local(cid,tools,32,'send','a2','sq-align512')
            if code:return code
            code=execute(cid,['python3',runtime+'/sq_align_pipeline_build.py',runtime],ROOT/'sq-align512-pipeline-build.log',450)
            if code:return code
            for n,attempt,seconds in ((16,'a2',1200),(32,'a2',1200),(4,'a6',900)):
                code=pipeline_phase(cid,tools,n,dict(attempt=attempt,build_variant='four-module-sq-align512'),seconds)
                if code:return code
            return local(cid,tools,4,'send','a3','sq-align512')
        if action=='sq-align-send16':
            assert tokens==16
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout
            code=execute(cid,['python3',runtime+'/sq_align_build.py'],ROOT/'sq-align512-build.log',450)
            report=ROOT/'sq-align512-a1/report.json'
            if report.is_file():
                summary=json.loads(report.read_text());summary.pop('commands',None);print(json.dumps(summary,separators=(',',':')),flush=True)
            if code:return code
            return local(cid,tools,16,'send','a3','sq-align512')
        if action=='cq-sq-snapshot':
            assert tokens==16
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout
            code=execute(cid,['python3',runtime+'/cq_snapshot.py','--include-sq'],ROOT/'cq-sq-snapshot16-runner.log',1000)
            if code:return code
            code=execute(cid,['python3',runtime+'/cq_snapshot_decode.py','--include-sq'],ROOT/'cq-sq-snapshot16-decode.log',160)
            report=ROOT/'cases/64p-t16-send-cq-sq-snapshot40us-a1/cq-sparse-report.json'
            if report.is_file():print(json.dumps(json.loads(report.read_text()),separators=(',',':')),flush=True)
            return code
        if action=='cq-snapshot-decode':
            assert tokens==16
            code=execute(cid,['python3',runtime+'/cq_snapshot_decode.py'],ROOT/'cq-snapshot16-decode.log',120)
            report=ROOT/'cases/64p-t16-send-cq-snapshot40us-a1/cq-sparse-report.json'
            if report.is_file():print(json.dumps(json.loads(report.read_text()),separators=(',',':')),flush=True)
            return code
        if action=='cq-snapshot':
            assert tokens==16
            active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
            assert active.returncode==0 and 'sstsim' not in active.stdout
            return execute(cid,['python3',runtime+'/cq_snapshot.py'],ROOT/'cq-snapshot16-runner.log',1000)
        if action=='pending-layout':
            import runpy
            runpy.run_path(str(tools/'pending_layout_remote.py'),run_name='__main__');return 0
        if action=='debug-pipeline':
            code=execute(cid,['python3',runtime+'/debug_pipeline.py',runtime],ROOT/'pipeline-debug-runner.log',400)
            report=ROOT/'pipeline-debug-result.json'
            if report.is_file():print(json.dumps(json.loads(report.read_text()),separators=(',',':')),flush=True)
            return code
        if action=='trace-pipeline':
            code=execute(cid,['python3',runtime+'/trace_pipeline.py'],ROOT/'pipeline-trace-runner.log',900)
            report=ROOT/'cases/64p-t4-pipeline-trace-a1/diagnostic.json'
            if report.is_file():
                data=json.loads(report.read_text());files=data.get('dump_files',[]);data['dump_file_count']=len(files);data['dump_files']=files[:12];print(json.dumps(data,separators=(',',':')),flush=True)
            return code
        if action=='independent-bs':
            first=request.get('start_index',0)
            assert tokens in (16,32,64,128) and isinstance(first,int) and 0<=first<6
            stages=('local-send','local-reduce','return-independent','inter-send-independent','final-independent','pipeline')
            if first==0:
                import importlib.util
                spec=importlib.util.spec_from_file_location('install_prepared',tools/'install_prepared.py')
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                asset,identity=module.install(ROOT,request)
                runtime_asset='/work/assets/'+asset.name
                code=execute(cid,['c++','-O2','-std=c++14','-DTILEXR_COMBINE_HT_SST_FP32=1','-DTILEXR_COMBINE_HT_SIM_SEND=1','-I/work/source/include',
                    '/work/source/tools/unitest/simulator/experiments/inter_server_send/sst/abi_probe.cpp','-o',runtime_asset+'/abi-probe'],ROOT/('abi-verify-%d.log'%tokens),120)
                assert code==0,'green_abi_probe_compile_failed'
                for rank,expected in enumerate(request['portable']['abis']):
                    p=subprocess.run(['docker','exec',cid,runtime_asset+'/abi-probe',str(rank),str(tokens),'128','6',str(64*tokens*6),str(64*tokens*6),'2026','-','64'],capture_output=True,timeout=10)
                    assert p.returncode==0 and json.loads(p.stdout)==expected,'blue_green_abi_difference'
                print(json.dumps(dict(stage='portable_fixture_installed',tokens=tokens,identity=identity),separators=(',',':')),flush=True)
            else:
                identity=json.loads((ROOT/'assets'/('64p-t%d'%tokens)/'identity.json').read_text())
                assert identity['candidate_commit']==REVISION and identity['kernel_binding']=='green_compiled_object' and identity['tokens']==tokens
            overall=True
            for stage in stages[:first]:
                saved=json.loads((ROOT/'cases'/('64p-t%d-%s-a1'%(tokens,stage))/'phase-result.json').read_text())
                assert saved['candidate_commit']==REVISION and saved['run']['status'] not in ('running','unknown'),'skipped_case_not_terminal'
                overall=overall and saved.get('operator_qualified',False)
            results=[]
            for stage in stages[first:]:
                active=subprocess.run(['docker','top',cid,'-eo','pid,comm'],capture_output=True,text=True,timeout=10)
                assert active.returncode==0 and 'sstsim' not in active.stdout,'another_sst_is_running'
                if stage.startswith('local-'):code=local(cid,tools,tokens,stage.split('-',1)[1])
                elif stage=='pipeline':code=pipeline_phase(cid,tools,tokens,request)
                else:code=return_phase(cid,tools,tokens,stage)
                results.append(dict(stage=stage,exit_code=code));overall=overall and code==0
                if code:
                    _,current=checked_container()
                    if not current['State']['Running']:
                        print(json.dumps(dict(stopped_on_container_exit=True,oom_killed=current['State']['OOMKilled'],results=results)),flush=True)
                        return 1
            print(json.dumps(dict(batch=tokens,overall_qualified=overall,stages_run=results),separators=(',',':')),flush=True)
            return 0 if overall else 1
        if action in ('prepare','smoke','matrix-case'):
            code=prepare_asset(cid,tools,tokens)
            if code:return code
            if action=='prepare':return 0
            if action=='smoke':return local(cid,tools,tokens,'send')
        if action=='qualify-local':
            for phase in ('reduce','fused'):
                code=local(cid,tools,tokens,phase)
                if code:return code
            return 0
        if action in ('return','qualify-return'):
            stages=[request['stage']] if action=='return' else ['return-independent','inter-send-independent','final-independent','return','inter-send','final-joint']
            first=request.get('start',0);assert isinstance(first,int) and 0<=first<len(stages)
            for stage in stages[:first]:
                candidates=list((ROOT/'cases').glob('64p-t%d-%s-*/phase-result.json'%(tokens,stage)))
                assert any(json.loads(p.read_text()).get('qualified') is True for p in candidates),'skipped_stage_not_qualified'
            for index,stage in enumerate(stages[first:],first):
                code=return_phase(cid,tools,tokens,stage,request.get('attempt','a1') if index==first else 'a1')
                if code:return code
            return 0
        if action=='local':return local(cid,tools,tokens,request['phase'],request.get('attempt','a1'),request.get('kernel_variant','original'))
        if action=='calibrate':
            reports=[]
            for ticks in (100000,200000):
                code=execute(cid,['python3',runtime+'/clock_fixture.py','--root','/work','--tools',runtime,'--wait-ticks',str(ticks)],ROOT/('prepare-clock-%d.log'%ticks),300)
                if code:
                    print(json.dumps({'prepare_exit':code,'wait_ticks':ticks}));return code
                case=ROOT/'cases'/('clock-%d-a1'%ticks)
                code=run_case(cid,case,tools,'check_clock.py','tilexr_ht_clock_probe',300)
                if code:return code
                reports.append(json.loads((case/'phase-result.json').read_text()))
            delta_us=reports[1]['platform_simulated_us']-reports[0]['platform_simulated_us']
            delta_ticks=reports[1]['profiling']['max_lane_ticks']-reports[0]['profiling']['max_lane_ticks']
            frequency=delta_ticks/delta_us*1000000
            assert delta_us>0 and 100000000<frequency<10000000000,'invalid_timer_calibration'
            report=dict(diagnostic_only=True,method='two_timer_waits_differenced_against_SST_simulation_time',
                frequency_hz=frequency,delta_ticks=delta_ticks,delta_sim_us=delta_us,cases=[r['case'] for r in reports])
            write(ROOT/'clock-calibration.json',report);print(json.dumps(report,separators=(',',':')));return 0
        if action in ('pipeline-build','qualify-pipeline'):
            code=execute(cid,['bash',runtime+'/pipeline_build.sh',runtime+'/pipeline_kernel.cpp'],ROOT/'pipeline-build.log',350)
            report=ROOT/'pipeline-build-40596e2-a1/build-result.json'
            if report.is_file():print(json.dumps(json.loads(report.read_text()),separators=(',',':')),flush=True)
            if code or action=='pipeline-build':return code
        if action in ('pipeline','qualify-pipeline','matrix-case','isolate-pipeline'):
            return pipeline_phase(cid,tools,tokens,request,900 if action=='isolate-pipeline' else None)
        if action in ('instrument','repair-profile-send'):
            code=execute(cid,['python3',runtime+'/instrument.py','/work'],ROOT/'instrument.log',650)
            if (ROOT/'profile-build/result.json').is_file():
                report=json.loads((ROOT/'profile-build/result.json').read_text())
                compact=dict(all_compiled=report['all_compiled'],targets={k:{f:v for f,v in x.items() if f!='commands'} for k,x in report['targets'].items()})
                print(json.dumps(compact,separators=(',',':')),flush=True)
            if code==0 and action=='repair-profile-send':return local(cid,tools,4,'send','a2')
            return code
        raise ValueError('phase_not_yet_implemented_'+action)
