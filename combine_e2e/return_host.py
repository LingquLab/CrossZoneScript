# Copyright (c) 2026 TileXR Project.
"""Run one isolated return-path kernel with pinned model and predecessor evidence."""
import hashlib,json,re,shutil,struct,subprocess,time
from pathlib import Path

ROOT=Path('/mnt/share/tilexr-sim-jobs/ea219fe0-2177-4850-84f0-c0da68a4a5fa/run-013cc3e-a1')
TEMPLATE=Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def diagnose_local_send():
    results={}
    for tokens in (4,16):
        case=ROOT/'cases'/('64p-t%d-local-send-a1'%tokens)
        row={'expected_payload_wqes':0,'expected_signal_wqes':0}
        for rank in range(64):
            p=case/'input_bin'/('rank%02d'%rank)/'prefill.bin'
            data=p.read_bytes()
            _,_,groups,chunks=struct.unpack_from('<4I',data)
            for index in range(8*groups*chunks):
                payload,count,signal,notify,reserved=struct.unpack_from('<3Q2I',data,512+index*32)
                row['expected_payload_wqes']+=count
                row['expected_signal_wqes']+=notify!=0
        log=case/'sim.log'
        size=log.stat().st_size
        row['log_bytes']=size
        row['counts_cover_full_log']=size<=67108864
        with log.open('rb') as f:
            if size>67108864:f.seek(max(0,size-1048576))
            text=f.read().decode(errors='replace')
        row['wrcqeresponse_records']=text.count('WrCQEResp')
        row['tail_lines']=[line[:250] for line in text.splitlines()[-6:]]
        row['dump_files']=len(list((case/'bin').glob('*.bin')))
        results[str(tokens)+'token']=row
    write(ROOT/'local-send-diagnostic.json',results)
    return results
def main(request):
    stage=request['stage']
    assert stage in ('return','inter-send','return-independent','inter-send-independent','final-independent','final-joint')
    identity=json.loads((ROOT/'identity.json').read_text())
    cid=identity['container_id']
    info=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
    assert info['Id']==cid and info['State']['Running'] and info['Image']==identity['image']
    assert info['HostConfig']['NetworkMode']=='none'
    diagnostic=diagnose_local_send() if stage=='final-independent' else None
    version=hashlib.sha256(json.dumps(request['files'],sort_keys=True).encode()).hexdigest()[:12]
    tools=ROOT/('return-tools-'+version)
    tools.mkdir(exist_ok=True)
    for name,text in request['files'].items():
        assert name in ('return_fixture.py','check_return.py','exports.py','send-abis.json')
        dest=tools/name
        if dest.exists():assert dest.read_text()==text
        else:dest.write_text(text)
    case=ROOT/'cases'/('64p-t16-'+stage+'-a1')
    runtime='/work/cases/'+case.name
    command=['docker','exec',cid,'python3','/work/'+tools.name+'/return_fixture.py',
             '--root','/work','--stage',stage,'--abis','/work/'+tools.name+'/send-abis.json']
    with (ROOT/('prepare-'+stage+'.log')).open('wb') as log:
        prepared=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=180)
    if prepared.returncode:
        result={'stage':stage,'prepare_exit':prepared.returncode,'qualified':False,'evidence':str(ROOT/('prepare-'+stage+'.log'))}
        if diagnostic is not None:result['local_send_diagnostic']=diagnostic
        write(ROOT/('prepare-'+stage+'-result.json'),result)
        print(json.dumps(result,separators=(',',':')))
        return 1
    meta=json.loads((case/'fixture-data/manifest.json').read_text())
    old=json.loads((TEMPLATE/'case-manifest.json').read_text())
    assert json.loads((TEMPLATE/'run.json').read_text())['status']=='sst_and_checker_passed'
    checker='source/tools/unitest/simulator/experiments/inter_server_send/fixture.py'
    for name,digest in old['files'].items():
        rel=Path(name)
        assert not rel.is_absolute() and '..' not in rel.parts
        if rel.parts[0] in ('input_bin','fixture-data','kernel') or name in ('workload.yml','export-layout.json',
                'case-manifest.json','blue-build-record.json','loader-manifest.json','runtime-layout.json','blue-case-identity.json'):continue
        src,dst=TEMPLATE/rel,case/rel
        assert sha(src)==digest
        dst.parent.mkdir(parents=True,exist_ok=True)
        if name==checker:shutil.copyfile(tools/'check_return.py',dst)
        elif name=='adapter/exports.py':shutil.copyfile(tools/'exports.py',dst)
        elif name=='adapter/run.py':
            s=src.read_text()
            needle='manifest["entry"] != "tilexr_ep_ht_inter_server_send_kernel"'
            assert s.count(needle)==1
            dst.write_text(s.replace(needle,'manifest["entry"] != '+json.dumps(meta['entry'])))
        elif name=='v310_config/workload_config.yml':continue
        else:shutil.copyfile(src,dst)
    shutil.copyfile(case/'workload.yml',case/'v310_config/workload_config.yml')
    fixture=case/'fixture-data'
    manifest=dict(old,candidate_commit=meta['candidate_commit'],kernel_sha256=meta['kernel_sha256'],
                  entry=meta['entry'],checker_sha256=sha(case/checker),variant=case.name)
    manifest['fixture_files']={str(p.relative_to(fixture)):sha(p) for p in fixture.rglob('*') if p.is_file()}
    manifest['files']={str(p.relative_to(case)):sha(p) for p in case.rglob('*') if p.is_file() and p.name!='case-manifest.json'}
    write(case/'case-manifest.json',manifest)
    write(ROOT/'progress.json',{'stage':'sst','case':str(case),'module':stage,'updated_unix':time.time()})
    command=['docker','exec',cid,'bash','-c','set -e; source /opt/sim/cann-env.sh; exec "$@"','bash',
             'python3',runtime+'/adapter/run.py','--case',runtime,'--fixture',runtime+'/fixture-data','--checker',runtime+'/'+checker,
             '--environment',runtime+'/sst-environment.json','--threads','8','--timeout','1200']
    start=time.monotonic()
    with (case/'runner.log').open('wb') as log:
        proc=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=1330)
    result={'stage':stage,'case':str(case),'runner_exit':proc.returncode,'wall_seconds':time.monotonic()-start,
            'candidate_commit':meta['candidate_commit'],'kernel_sha256':meta['kernel_sha256'],
            'routes_sha256':meta['routes_sha256'],'consumed_actual_sha256':meta['consumed_actual_sha256'],
            'input_mode':meta['input_mode'],'consumed_input_sha256':meta['consumed_input_sha256']}
    if diagnostic is not None:result['local_send_diagnostic']=diagnostic
    if (case/'run.json').is_file():
        run=json.loads((case/'run.json').read_text())
        result['run']={k:run[k] for k in ('status','returncode','guards') if k in run}
    for name in ('check.log','runner.log'):
        path=case/name
        if not path.is_file():continue
        text=path.read_text(errors='replace').strip()
        if name=='check.log':
            try:result['checked']=json.loads(text)
            except ValueError:pass
        errors=[s[:250] for s in text.splitlines() if re.search(r'(?:Error:|error:|mismatch|not found|No such file)',s)]
        if errors:result[name+'_error']=errors[-1]
    log=case/'sim.log'
    if log.is_file():
        with log.open('rb') as f:
            f.seek(max(0,log.stat().st_size-65536));tail=f.read().decode(errors='replace')
        match=re.search(r'Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)',tail)
        if match:result['sim_us']=float(match[1])*{'ns':.001,'us':1,'ms':1000,'s':1000000}[match[2]]
        result['model_errors']=[s[:250] for s in tail.splitlines() if re.search(r'(?i)fatal|segmentation fault|assertion',s)][-3:]
    result['qualified']=proc.returncode==0 and result.get('run',{}).get('status')=='sst_and_checker_passed' and 'checked' in result
    write(case/'phase-result.json',result)
    write(ROOT/'progress.json',{'stage':'phase_finished','case':str(case),'qualified':result['qualified'],'updated_unix':time.time()})
    print(json.dumps(result,separators=(',',':')))
    return 0 if result['qualified'] else 1
