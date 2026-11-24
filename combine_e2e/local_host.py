# Copyright (c) 2026 TileXR Project.
"""Install an immutable route fixture and run one bounded SST stage."""
import base64,gzip,hashlib,io,json,re,shutil,struct,subprocess,tarfile,time
from pathlib import Path,PurePosixPath

ROOT=Path('/mnt/share/tilexr-sim-jobs/ea219fe0-2177-4850-84f0-c0da68a4a5fa/run-013cc3e-a1')
TEMPLATE=Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549')
REVISION='013cc3e2f8abf8157abdc609afcfe2369806b1e1'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,d): p.write_text(json.dumps(d,indent=2)+'\n')
def main(request):
    identity=json.loads((ROOT/'identity.json').read_text())
    container=identity['container_id']
    info=json.loads(subprocess.check_output(['docker','inspect',container]))[0]
    assert info['Id']==container and info['Image']==identity['image'] and info['State']['Running']
    assert info['HostConfig']['NetworkMode']=='none'
    world,tokens,phase=request['world'],request['tokens'],request['phase']
    assert world in (16,32,64) and tokens in (4,16) and phase in ('send','reduce','fused')
    case=ROOT/'cases'/('%dp-t%d-local-%s-a1'%(world,tokens,phase))
    case.mkdir(parents=True,exist_ok=False)
    runtime='/work/cases/'+case.name
    raw=base64.b64decode(request['archive'],validate=True)
    assert hashlib.sha256(raw).hexdigest()==request['identity']['archive_sha256']
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        for member in archive:
            rel=PurePosixPath(member.name)
            assert member.isfile() and not rel.is_absolute() and '..' not in rel.parts
            assert rel.parts[0] in ('workload.yml','export-layout.json','input_bin','fixture-data','routes.json')
            target=case/str(rel)
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as f: shutil.copyfileobj(archive.extractfile(member),f)
    old_manifest=json.loads((TEMPLATE/'case-manifest.json').read_text())
    assert json.loads((TEMPLATE/'run.json').read_text())['status']=='sst_and_checker_passed'
    checker='source/tools/unitest/simulator/experiments/inter_server_send/fixture.py'
    exporter=ROOT/'source/tools/unitest/simulator/experiments/inter_server_send/sst/exports.py'
    for name,digest in old_manifest['files'].items():
        rel=Path(name)
        assert not rel.is_absolute() and '..' not in rel.parts
        if rel.parts[0] in ('input_bin','fixture-data') or name in ('workload.yml','export-layout.json','blue-build-record.json','case-manifest.json'): continue
        src,dst=TEMPLATE/rel,case/rel
        assert sha(src)==digest
        dst.parent.mkdir(parents=True,exist_ok=True)
        if name==checker: dst.write_text(request['checker'])
        elif name=='adapter/exports.py': shutil.copyfile(exporter,dst)
        elif name=='adapter/run.py':
            s=src.read_text()
            needle='manifest["entry"] != "tilexr_ep_ht_inter_server_send_kernel"'
            assert s.count(needle)==1
            dst.write_text(s.replace(needle,'manifest["entry"] != "tilexr_moonep_combine_ht_kernel"'))
        elif name in ('kernel/inter_server_send.o','v310_config/workload_config.yml'): continue
        else: shutil.copyfile(src,dst)
    kernel=case/'kernel/inter_server_send.o'
    kernel.parent.mkdir(exist_ok=True)
    compiled=ROOT/'build-fp32/src/moonep/combine_ht/tilexr_moonep_combine_ht_kernel.o'
    assert sha(compiled)=='97069d2af16a17694f6fc63b37d196d531b6d37fa8e54e3daff7e5e56affc2e9'
    data=compiled.read_bytes()
    kernel.write_bytes(data+bytes(10485760-len(data)))
    assert sha(kernel)==request['identity']['kernel_sha256']
    workload=json.loads((case/'workload.yml').read_text().replace(request['identity']['runtime_root'],runtime))
    layout=json.loads((case/'export-layout.json').read_text())
    if phase=='reduce':
        prior=case.with_name('%dp-t%d-local-send-a1'%(world,tokens))
        assert json.loads((prior/'run.json').read_text())['status']=='sst_and_checker_passed'
    for row in layout['ranks']:
        rank=row['rank']
        folder=case/'input_bin'/('rank%02d'%rank)
        args=bytearray((folder/'kernel_args.bin').read_bytes())
        assert struct.unpack_from('<I',args,156)[0]==3
        struct.pack_into('<I',args,156,{'send':1,'reduce':2,'fused':3}[phase])
        (folder/'kernel_args.bin').write_bytes(args)
        params=workload['workload_config']['chip%d'%rank]['kernel_param']
        params['word19']['value']=struct.unpack_from('<Q',args,152)[0]
        if phase=='reduce':
            workspace=bytearray((folder/'workspace.bin').read_bytes())
            for field in ('returned','completed','output'):
                bounds=row['exports'][field]
                payload=(prior/'actual'/('rank%02d'%rank)/(field+'.bin')).read_bytes()
                assert len(payload)==bounds['size']
                offset=bounds['addr']-struct.unpack_from('<Q',args,0)[0]
                workspace[offset:offset+len(payload)]=payload
            (folder/'workspace.bin').write_bytes(workspace)
    write(case/'workload.yml',workload)
    shutil.copyfile(case/'workload.yml',case/'v310_config/workload_config.yml')
    fixture=case/'fixture-data'
    meta=json.loads((fixture/'manifest.json').read_text())
    meta['phase']=phase
    write(fixture/'manifest.json',meta)
    manifest=dict(old_manifest,candidate_commit=REVISION,entry='tilexr_moonep_combine_ht_kernel',
                  kernel_sha256=sha(kernel),checker_sha256=sha(case/checker),variant=case.name)
    manifest['fixture_files']={str(p.relative_to(fixture)):sha(p) for p in fixture.rglob('*') if p.is_file()}
    manifest['files']={str(p.relative_to(case)):sha(p) for p in case.rglob('*') if p.is_file() and p.name!='case-manifest.json'}
    write(case/'case-manifest.json',manifest)
    write(ROOT/'progress.json',{'stage':'sst','case':str(case),'phase':phase,'updated_unix':time.time()})
    command=['docker','exec',container,'bash','-c','set -e; source /opt/sim/cann-env.sh; exec "$@"','bash',
             'python3',runtime+'/adapter/run.py','--case',runtime,'--fixture',runtime+'/fixture-data',
             '--checker',runtime+'/'+checker,'--environment',runtime+'/sst-environment.json','--threads','8','--timeout','900']
    start=time.monotonic()
    with (case/'runner.log').open('wb') as log:
        proc=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=1030)
    result={'case':str(case),'world':world,'tokens':tokens,'hidden':128,'topk':6,'experts':256,'phase':phase,
            'runner_exit':proc.returncode,'wall_seconds':time.monotonic()-start,'candidate_commit':REVISION,
            'kernel_sha256':sha(kernel),'routes_sha256':meta['routes_sha256']}
    if (case/'run.json').is_file():
        run=json.loads((case/'run.json').read_text())
        result['run']={k:run[k] for k in ('status','returncode','guards') if k in run}
    if (case/'check.log').is_file():
        text=(case/'check.log').read_text(errors='replace').strip()
        try: result['checked']=json.loads(text)
        except ValueError:
            errors=[line for line in text.splitlines() if line.startswith(('ValueError:','AssertionError:'))]
            result['first_error']=errors[-1][:300] if errors else 'see_check_log'
    log=case/'sim.log'
    if log.is_file():
        with log.open('rb') as f:
            f.seek(max(0,log.stat().st_size-65536))
            tail=f.read().decode(errors='replace')
        match=re.search(r'Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)',tail)
        if match: result['sim_us']=float(match[1])*{'ns':.001,'us':1,'ms':1000,'s':1000000}[match[2]]
        result['model_errors']=[word for word in ('fatal','segmentation fault','assertion') if word in tail.lower()]
    result['qualified']=proc.returncode==0 and result.get('run',{}).get('status')=='sst_and_checker_passed' and 'checked' in result
    write(case/'phase-result.json',result)
    write(ROOT/'progress.json',{'stage':'phase_finished','case':str(case),'qualified':result['qualified'],'updated_unix':time.time()})
    print(json.dumps(result,separators=(',',':')))
    return 0 if result['qualified'] else 1
