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
            dst.write_text(s.replace(needle,'manifest["entry"] != '+repr(entry)))
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
        'wall_seconds':time.monotonic()-mono,'started_unix':started,'finished_unix':time.time(),
        'kernel_sha256':sha(case/'kernel/inter_server_send.o'),'routes_sha256':meta['routes_sha256'],
        'platform_simulated_us':None,'profiling_operator_us':None}
    if (case/'run.json').is_file():
        r=json.loads((case/'run.json').read_text());result['run']=r
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
        profile_code=execute(cid,['python3','-c',"import sys,json;sys.path.insert(0,"+repr('/work/'+tools.name)+");import profile;print(json.dumps(profile.summarize("+repr(runtime)+","+str(meta.get('profiling_capacity',8192))+","+str(meta.get('blocks',16))+")))"],case/'profiling-check.log',120)
        if profile_code==0:result['profiling']=json.loads((case/'profiling-check.log').read_text())
        else:result['profiling_error']='see_profiling-check.log'
    result['qualified']=result['numerical_qualified'] and 'profiling' in result
    result['run']={k:v for k,v in result.get('run',{}).items() if k in ('status','returncode','guards','error','started_unix','finished_unix','elapsed_seconds')}
    write(case/'phase-result.json',result);write(ROOT/'progress.json',dict(stage='finished',case=str(case),qualified=result['qualified'],updated_unix=time.time()))
    print(json.dumps(result,separators=(',',':')),flush=True)
    return 0 if result['qualified'] else 1

def local(cid,tools,tokens,phase):
    assert phase in ('send','reduce','fused')
    asset=ROOT/'assets'/('64p-t%d'%tokens)
    identity=json.loads((asset/'identity.json').read_text())
    case=ROOT/'cases'/('64p-t%d-local-%s-a1'%(tokens,phase));case.mkdir(parents=True,exist_ok=False)
    for name in ('input_bin','kernel'):shutil.copytree(asset/'loader'/name,case/name)
    shutil.copytree(asset/'fixture-data',case/'fixture-data');shutil.copyfile(asset/'routes.json',case/'routes.json')
    layout=json.loads((asset/'loader/export-layout.json').read_text());layout['required_exports']=['returned','completed','status','output','profiling']
    write(case/'export-layout.json',layout)
    runtime='/work/cases/'+case.name
    workload=json.loads((asset/'loader/workload.yml').read_text().replace(identity['runtime_root'],runtime))
    provenance=hashlib.sha256()
    if phase=='reduce':
        prior=case.with_name('64p-t%d-local-send-a1'%tokens)
        assert json.loads((prior/'phase-result.json').read_text())['qualified'],'predecessor_not_qualified'
    for row in layout['ranks']:
        folder=case/'input_bin'/('rank%02d'%row['rank']);args=bytearray((folder/'kernel_args.bin').read_bytes())
        struct.pack_into('<I',args,156,{'send':1,'reduce':2,'fused':3}[phase]);(folder/'kernel_args.bin').write_bytes(args)
        workload['workload_config']['chip%d'%row['rank']]['kernel_param']['word19']['value']=struct.unpack_from('<Q',args,152)[0]
        if phase=='reduce':
            ws=bytearray((folder/'workspace.bin').read_bytes())
            for field in ('returned','completed','output'):
                bounds=row['exports'][field];payload=(prior/'actual'/folder.name/(field+'.bin')).read_bytes()
                assert len(payload)==bounds['size'];offset=bounds['addr']-struct.unpack_from('<Q',args,0)[0]
                ws[offset:offset+len(payload)]=payload;provenance.update(hashlib.sha256(payload).digest())
            (folder/'workspace.bin').write_bytes(ws)
    write(case/'workload.yml',workload)
    meta=json.loads((case/'fixture-data/manifest.json').read_text());meta.update(phase=phase,
        input_mode='actual_predecessor_outputs' if phase=='reduce' else 'expert_outputs_and_routes',
        consumed_actual_sha256=provenance.hexdigest() if phase=='reduce' else None)
    write(case/'fixture-data/manifest.json',meta)
    return run_case(cid,case,tools,'check_local.py','tilexr_moonep_combine_ht_kernel')

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

def main(request):
    cid,info=checked_container();action=request['action'];assert info['State']['Running'] or action in ('status','stop')
    if action=='status':return status(cid,info)
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
        tokens=request.get('tokens',4);assert tokens in (4,16,64,128)
        if tokens!=4 and action not in ('instrument','pipeline-build'):
            gate=ROOT/'cases/64p-t4-pipeline-a1/phase-result.json'
            assert gate.is_file() and json.loads(gate.read_text())['qualified'],'bs4_joint_gate'
        if action in ('prepare','smoke'):
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
            if code==0 and action=='smoke':return local(cid,tools,tokens,'send')
            return code
        if action=='qualify-local':
            for phase in ('reduce','fused'):
                code=local(cid,tools,tokens,phase)
                if code:return code
            return 0
        if action=='return':
            stage=request['stage'];assert stage in ('return-independent','return','inter-send-independent','inter-send','final-independent','final-joint')
            code=execute(cid,['python3',runtime+'/return_fixture.py','--root','/work','--stage',stage,'--tokens',str(tokens),
                '--abis','/work/assets/64p-t%d/send-abis.json'%tokens],ROOT/('prepare-%d-%s.log'%(tokens,stage)),500)
            if code:
                print(json.dumps({'prepare_exit':code,'stage':stage,'log':str(ROOT/('prepare-%d-%s.log'%(tokens,stage)))}));return code
            case=ROOT/'cases'/('64p-t%d-%s-a1'%(tokens,stage));meta=json.loads((case/'fixture-data/manifest.json').read_text())
            return run_case(cid,case,tools,'check_return.py',meta['entry'],1000)
        if action=='local':return local(cid,tools,tokens,request['phase'])
        if action=='instrument':
            code=execute(cid,['python3',runtime+'/instrument.py','/work'],ROOT/'instrument.log',650)
            if (ROOT/'profile-build/result.json').is_file():print((ROOT/'profile-build/result.json').read_text())
            return code
        raise ValueError('phase_not_yet_implemented_'+action)
