"""Combine verified SQ boundaries with compiler memory barriers around CQ DCCI."""
import hashlib,json,subprocess
from pathlib import Path
ROOT=Path('/work')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main(tools):
    tools=Path(tools)
    prior=json.loads((ROOT/'cases/64p-t16-pipeline-a3/phase-result.json').read_text())
    assert prior['run']['status']=='timed_out' and prior['kernel_compatibility_variant']=='sq-align-all512'
    api=json.loads((ROOT/'cq-compiler-barrier-a2/report.json').read_text())
    assert api['exit_codes']==[0,0] and not api['text_identical']
    api_root=ROOT/'cq-compiler-barrier-a2/include'
    assert sha(api_root/'tilexr/comm/udma/device/api.h')==api['overlay_header_sha256']
    assert api['overlay_header_sha256']=='bd9c87e965d2fe712693048c5a5dd3f2f58ee202f67c64bf5f95ced280f049f3'
    assert sha(ROOT/'source/include/tilexr/comm/udma/device/api.h')==api['baseline_header_sha256']
    baseline=json.loads((ROOT/'pipeline-four-module-sq-align-all512-a1/build-result.json').read_text())
    source=ROOT/'inter-sq-align512-a1/joint.cpp';assert sha(source)==baseline['source_sha256']
    assert sha(ROOT/'inter-sq-align512-a1/inter_server_send_kernel.cpp')==baseline['compatibility_inter_source_sha256']
    local_root=ROOT/'sq-align512-a1/include'
    assert sha(local_root/'combine_ht_kernel.h')==baseline['compatibility_header_sha256']
    target=ROOT/'pipeline-four-module-sq-align-all512-cq-a1';assert not target.exists()
    script=(tools/'pipeline_build.sh').read_text()
    assert script.count('target=/work/pipeline-build-40596e2-a1')==1
    assert script.count('includes=("-I$(dirname "$1")"')==1
    script=script.replace('target=/work/pipeline-build-40596e2-a1','target='+str(target))
    script=script.replace('includes=("-I$(dirname "$1")"','includes=("-I'+str(local_root)+'" "-I'+str(api_root)+'" "-I'+str(tools)+'" "-I$(dirname "$1")"')
    builder=ROOT/'pipeline-four-module-sq-align-all512-cq-build.sh';assert not builder.exists();builder.write_text(script)
    with (ROOT/'pipeline-four-module-sq-align-all512-cq-compile.log').open('wb') as log:
        code=subprocess.run(['bash',str(builder),str(source)],stdout=log,stderr=subprocess.STDOUT,timeout=300).returncode
    if code:return code
    report=json.loads((target/'build-result.json').read_text())
    report.update(source_sha256=sha(source),baseline_pipeline_object_sha256=baseline['sha256'],
        pipeline_build_variant='four-module-sq-align-all512-cq',compiler_scalar_dcci=False,
        stage_isolation_noinline=True,barrier_algorithm='leader_release',extra_round_fence=False,
        kernel_compatibility_variant='sq-align-all512-cq-barrier',
        compatibility_header_sha256=baseline['compatibility_header_sha256'],
        compatibility_baseline_header_sha256=baseline['compatibility_baseline_header_sha256'],
        compatibility_inter_source_sha256=baseline['compatibility_inter_source_sha256'],
        compatibility_api_header_sha256=api['overlay_header_sha256'],compatibility_baseline_api_header_sha256=api['baseline_header_sha256'])
    (target/'build-result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,separators=(',',':')),flush=True);return 0
if __name__=='__main__':
    import sys
    raise SystemExit(main(sys.argv[1]))
