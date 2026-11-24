# Copyright (c) 2026 TileXR Project.
"""Prepare and run the complete one-invocation HT simulator fixture."""
import hashlib,json,re,shutil,subprocess,time
from pathlib import Path
ROOT=Path('/mnt/share/tilexr-sim-jobs/ea219fe0-2177-4850-84f0-c0da68a4a5fa/run-013cc3e-a1')
TEMPLATE=Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def main(files):
    identity=json.loads((ROOT/'identity.json').read_text())
    cid=identity['container_id'];info=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
    assert info['State']['Running'] and info['Image']==identity['image'] and info['HostConfig']['NetworkMode']=='none'
    tools=ROOT/'pipeline-case-tools-a1';tools.mkdir(exist_ok=False)
    for name,text in files.items():
        assert name in ('pipeline_fixture.py','prefill.cpp','check_pipeline.py','exports.py','send-abis.json')
        (tools/name).write_text(text)
    case=ROOT/'cases/64p-t4-pipeline-a1';runtime='/work/cases/'+case.name
    command=['docker','exec',cid,'python3','/work/'+tools.name+'/pipeline_fixture.py','--root','/work',
             '--tools','/work/'+tools.name,'--abis','/work/'+tools.name+'/send-abis.json']
    with (ROOT/'pipeline-prepare.log').open('wb') as log:p=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=240)
    if p.returncode:
        result={'prepare_exit':p.returncode,'joint_single_invocation':True,'qualified':False,'evidence':str(ROOT/'pipeline-prepare.log')}
        write(ROOT/'pipeline-prepare-result.json',result);print(json.dumps(result,separators=(',',':')));return 1
    meta=json.loads((case/'fixture-data/manifest.json').read_text())
    template=json.loads((TEMPLATE/'case-manifest.json').read_text())
    assert json.loads((TEMPLATE/'run.json').read_text())['status']=='sst_and_checker_passed'
    checker='source/tools/unitest/simulator/experiments/inter_server_send/fixture.py'
    for name,digest in template['files'].items():
        rel=Path(name);assert not rel.is_absolute() and '..' not in rel.parts
        if rel.parts[0] in ('input_bin','fixture-data','kernel') or name in ('workload.yml','export-layout.json','case-manifest.json',
                'blue-build-record.json','loader-manifest.json','runtime-layout.json','blue-case-identity.json'):continue
        src,dst=TEMPLATE/rel,case/rel;assert sha(src)==digest;dst.parent.mkdir(parents=True,exist_ok=True)
        if name==checker:shutil.copyfile(tools/'check_pipeline.py',dst)
        elif name=='adapter/exports.py':shutil.copyfile(tools/'exports.py',dst)
        elif name=='adapter/run.py':
            text=src.read_text();needle='manifest["entry"] != "tilexr_ep_ht_inter_server_send_kernel"';assert text.count(needle)==1
            dst.write_text(text.replace(needle,'manifest["entry"] != "tilexr_ht_pipeline_sst"'))
        elif name=='v310_config/workload_config.yml':continue
        else:shutil.copyfile(src,dst)
    shutil.copyfile(case/'workload.yml',case/'v310_config/workload_config.yml')
    fixture=case/'fixture-data'
    manifest=dict(template,entry=meta['entry'],kernel_sha256=meta['kernel_sha256'],candidate_commit=meta['candidate_commit'],
                  checker_sha256=sha(case/checker),variant=case.name,joint_single_invocation=True)
    manifest['fixture_files']={str(p.relative_to(fixture)):sha(p) for p in fixture.rglob('*') if p.is_file()}
    manifest['files']={str(p.relative_to(case)):sha(p) for p in case.rglob('*') if p.is_file() and p.name!='case-manifest.json'}
    write(case/'case-manifest.json',manifest)
    write(ROOT/'progress-pipeline.json',{'stage':'sst','case':str(case),'updated_unix':time.time()})
    command=['docker','exec',cid,'bash','-c','set -e; source /opt/sim/cann-env.sh; exec "$@"','bash',
             'python3',runtime+'/adapter/run.py','--case',runtime,'--fixture',runtime+'/fixture-data','--checker',runtime+'/'+checker,
             '--environment',runtime+'/sst-environment.json','--threads','8','--timeout','1200']
    start=time.monotonic()
    with (case/'runner.log').open('wb') as log:p=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=1340)
    result={'joint_single_invocation':True,'case':str(case),'runner_exit':p.returncode,'wall_seconds':time.monotonic()-start,
            'candidate_commit':meta['candidate_commit'],'kernel_sha256':meta['kernel_sha256'],'routes_sha256':meta['routes_sha256']}
    if (case/'run.json').is_file():
        record=json.loads((case/'run.json').read_text());result['run']={k:record[k] for k in ('status','returncode','guards') if k in record}
    for filename in ('check.log','runner.log'):
        f=case/filename
        if f.is_file():
            text=f.read_text(errors='replace').strip()
            if filename=='check.log':
                try:result['checked']=json.loads(text)
                except ValueError:pass
            errors=[line[:250] for line in text.splitlines() if re.search(r'Error:|error:|mismatch|not found|No such file',line)]
            if errors:result[filename+'_error']=errors[-1]
    f=case/'sim.log'
    if f.is_file():
        with f.open('rb') as log:log.seek(max(0,f.stat().st_size-65536));tail=log.read().decode(errors='replace')
        match=re.search(r'Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)',tail)
        if match:result['sim_us']=float(match[1])*{'ns':.001,'us':1,'ms':1000,'s':1000000}[match[2]]
        result['model_errors']=[line[:250] for line in tail.splitlines() if re.search(r'(?i)fatal|segmentation fault|assertion',line)][-3:]
    result['qualified']=p.returncode==0 and result.get('run',{}).get('status')=='sst_and_checker_passed' and 'checked' in result
    write(case/'pipeline-result.json',result);print(json.dumps(result,separators=(',',':')));return 0 if result['qualified'] else 1
