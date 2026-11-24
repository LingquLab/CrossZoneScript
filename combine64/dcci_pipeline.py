# Copyright (c) 2026 TileXR Project.
"""Prepare a one-option compiler comparison against the preserved joint-kernel input."""
import hashlib,json,os,shutil,subprocess
from pathlib import Path
ROOT=Path('/work');BASE=ROOT/'cases/64p-t4-pipeline-a1';CASE=ROOT/'cases/64p-t4-pipeline-dcci-diagnostic-a1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main(tools):
    tools=Path(tools);target=ROOT/'pipeline-scalar-dcci-a1';assert not target.exists() and not CASE.exists()
    original=ROOT/'pipeline-build-40596e2-a1'
    assert (original/'pipeline_kernel.cpp').read_bytes()==(tools/'pipeline_kernel.cpp').read_bytes()
    script=(tools/'pipeline_build.sh').read_text().replace('target=/work/pipeline-build-40596e2-a1','target=/work/pipeline-scalar-dcci-a1')
    assert script.count('-cce-aicore-dcci-insert-for-scalar=false')==1
    script=script.replace('-cce-aicore-dcci-insert-for-scalar=false','-cce-aicore-dcci-insert-for-scalar=true')
    builder=ROOT/'pipeline-scalar-dcci-a1.sh';builder.write_text(script)
    with (ROOT/'pipeline-scalar-dcci-compile.log').open('wb') as log:p=subprocess.run(['bash',str(builder),str(tools/'pipeline_kernel.cpp')],stdout=log,stderr=subprocess.STDOUT,timeout=300)
    if p.returncode:return p.returncode
    report=json.loads((target/'build-result.json').read_text());report['compiler_scalar_dcci']=True;report['source_sha256']=sha(tools/'pipeline_kernel.cpp')
    (target/'build-result.json').write_text(json.dumps(report,indent=2))
    manifest=json.loads((BASE/'case-manifest.json').read_text());CASE.mkdir()
    for name,digest in manifest['files'].items():
        rel=Path(name);assert not rel.is_absolute() and '..' not in rel.parts
        source,dest=BASE/rel,CASE/rel;assert sha(source)==digest;dest.parent.mkdir(parents=True,exist_ok=True)
        if rel.parts[0] in ('input_bin','fixture-data','kernel'):os.link(source,dest)
        else:shutil.copyfile(source,dest)
    kernel=CASE/'kernel/inter_server_send.o';kernel.unlink();binary=(target/'kernel.o').read_bytes();kernel.write_bytes(binary+bytes(10485760-len(binary)))
    meta_path=CASE/'fixture-data/manifest.json';meta=json.loads(meta_path.read_text());meta_path.unlink()
    meta.update(diagnostic_only=True,operator_qualified=False,local_profile_expected=False,
        diagnostic_scope='original_input_only_compiler_scalar_DCCI_enabled',kernel_sha256=sha(kernel),compiler_scalar_dcci=True)
    meta_path.write_text(json.dumps(meta,indent=2))
    workload=(CASE/'workload.yml').read_text().replace(str(BASE),str(CASE));(CASE/'workload.yml').write_text(workload)
    shutil.copyfile(CASE/'workload.yml',CASE/'v310_config/workload_config.yml')
    args=(CASE/'input_bin/rank32/kernel_args.bin').read_bytes();assert args==(BASE/'input_bin/rank32/kernel_args.bin').read_bytes()
    print(json.dumps({'prepared':True,'diagnostic_only':True,'case':str(CASE),'build':report},separators=(',',':')),flush=True)
    return 0
if __name__=='__main__':
    import sys
    raise SystemExit(main(sys.argv[1]))
