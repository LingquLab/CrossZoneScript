# Copyright (c) 2026 TileXR Project.
"""Reproduce the preserved pipeline fault with instruction dumps restricted to rank 32."""
import hashlib,json,os,shutil,signal,subprocess,time
from collections import deque
from pathlib import Path
import yaml
ROOT=Path('/work');BASE=ROOT/'cases/64p-t4-pipeline-a1';CASE=ROOT/'cases/64p-t4-pipeline-trace-a1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def main():
    original=json.loads((BASE/'case-manifest.json').read_text())
    assert original['candidate_commit']=='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3'
    assert json.loads((BASE/'run.json').read_text())['returncode']==255
    CASE.mkdir(exist_ok=False)
    for name,digest in original['files'].items():
        p=Path(name);assert not p.is_absolute() and '..' not in p.parts
        source,target=BASE/p,CASE/p;assert sha(source)==digest;target.parent.mkdir(parents=True,exist_ok=True)
        if p.parts[0] in ('input_bin','kernel','fixture-data'):os.link(source,target)
        else:shutil.copyfile(source,target)
    workload=json.loads((CASE/'workload.yml').read_text().replace(str(BASE),str(CASE)))
    write(CASE/'workload.yml',workload);shutil.copyfile(CASE/'workload.yml',CASE/'v310_config/workload_config.yml')
    config=CASE/'v310_config/davidV100_simulator_config.yml';doc=yaml.safe_load(config.read_text())
    assert 'architecture_config' in doc and 'simulator_config' in doc
    doc['architecture_config']['chip_dump_enable_list']=[32]
    def enable(value):
        if isinstance(value,dict):
            for key in value:
                if key in ('enable_dump','enable_dump_detail'):value[key]=1
                else:enable(value[key])
        elif isinstance(value,list):
            for item in value:enable(item)
    enable(doc);config.write_text(yaml.safe_dump(doc,sort_keys=False))
    environment=json.loads((CASE/'sst-environment.json').read_text())
    for k in ('sst_bin','env_script','decoder_library'):assert sha(Path(environment[k]))==environment[k+'_sha256']
    for p,digest in environment.get('runtime_files',{}).items():assert sha(Path(p))==digest
    record=dict(diagnostic_only=True,operator_qualified=False,candidate_commit=original['candidate_commit'],
        kernel_sha256=sha(CASE/'kernel/inter_server_send.o'),input_source=str(BASE),dump_chips=[32],wall_limit_seconds=780,dump_limit_bytes=4294967296,
        configuration_sha256=sha(config),started_unix=time.time())
    command=['bash','-c','set -e; source "$1"; shift; exec "$@"','bash',environment['env_script'],environment['sst_bin'],'-n','8',str(CASE/'test_UBusSim.py')]
    start=time.monotonic()
    with (CASE/'sim.log').open('wb') as log:
        child=subprocess.Popen(command,cwd=CASE,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        record['pid']=child.pid;write(CASE/'diagnostic.json',record)
        while child.poll() is None:
            total=sum(p.stat().st_size for p in (CASE/'dumps').rglob('*') if p.is_file()) if (CASE/'dumps').exists() else 0
            if time.monotonic()-start>780 or total>4294967296:
                record['stop_reason']='wall_limit' if time.monotonic()-start>780 else 'dump_limit'
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=15)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=10)
                break
            time.sleep(2)
    record.update(returncode=child.returncode,sst_wall_seconds=time.monotonic()-start,finished_unix=time.time())
    fatal=[]
    with (CASE/'sim.log').open(errors='replace') as stream:
        for line in stream:
            if 'FATAL' in line or 'Unknown low level' in line:
                fatal.append(line.strip()[:450])
                if len(fatal)==2:break
    record['fatal']=fatal;record['dump_files']=[]
    for p in (CASE/'dumps').rglob('*') if (CASE/'dumps').exists() else []:
        if p.is_file():record['dump_files'].append(dict(path=str(p.relative_to(CASE)),bytes=p.stat().st_size))
    record['fault_core_excerpts']=[]
    for row in record['dump_files']:
        if '32_aicore3_aiv0_' in row['path']:
            p=CASE/row['path']
            with p.open('rb') as f:f.seek(max(0,p.stat().st_size-32768));tail=f.read().decode(errors='replace').splitlines()
            record['fault_core_excerpts'].append(dict(path=row['path'],tail=[s[:550] for s in tail[-6:]]))
    write(CASE/'diagnostic.json',record)
    summary=dict(record);summary['dump_files']=record['dump_files'][:12];summary['dump_file_count']=len(record['dump_files'])
    print(json.dumps(summary,separators=(',',':')),flush=True)
    return 0
if __name__=='__main__':raise SystemExit(main())
