# Copyright (c) 2026 TileXR Project.
"""Prepare the verified four-module wrapper with the scoped CQ header overlay."""
import hashlib,json,subprocess
from pathlib import Path

ROOT=Path('/work')
SOURCE_SHA='1b3b99dcfdc77181e1937eec90c78285c478433ba312116127417747ebd41253'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main(tools):
    tools=Path(tools)
    send=json.loads((ROOT/'cases/64p-t16-local-send-a2/phase-result.json').read_text())
    assert send['operator_qualified'] and send['qualified'] and send['kernel_compatibility_variant']=='cq-memory-barrier'
    assert send['candidate_commit']=='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3' and send['world']==64 and send['tokens']==16
    comparison=json.loads((ROOT/'cq-compiler-barrier-a2/report.json').read_text())
    assert comparison['exit_codes']==[0,0] and comparison['text_identical'] is False
    local=(ROOT/'cq-compiler-barrier-a2/local_ht.o').read_bytes()
    assert hashlib.sha256(local).hexdigest()==comparison['overlay_object_sha256']
    assert hashlib.sha256(local+bytes(10485760-len(local))).hexdigest()==send['kernel_sha256']
    overlay=ROOT/'cq-compiler-barrier-a2/include'
    assert sha(overlay/'tilexr/comm/udma/device/api.h')==comparison['overlay_header_sha256']
    assert sha(ROOT/'source/include/tilexr/comm/udma/device/api.h')==comparison['baseline_header_sha256']
    source=ROOT/'pipeline-four-module-joint-source.cpp'
    assert sha(source)==SOURCE_SHA
    baseline=json.loads((ROOT/'pipeline-four-module-joint-a1/build-result.json').read_text())
    assert baseline['source_sha256']==SOURCE_SHA and sha(ROOT/'pipeline-four-module-joint-a1/kernel.o')==baseline['sha256']
    dest=ROOT/'pipeline-four-module-cq-a1';assert not dest.exists()
    script=(tools/'pipeline_build.sh').read_text()
    assert script.count('target=/work/pipeline-build-40596e2-a1')==1
    assert script.count('includes=("-I$(dirname "$1")"')==1
    script=script.replace('target=/work/pipeline-build-40596e2-a1','target='+str(dest))
    script=script.replace('includes=("-I$(dirname "$1")"',
        'includes=("-I'+str(overlay)+'" "-I'+str(tools)+'" "-I$(dirname "$1")"')
    builder=ROOT/'pipeline-four-module-cq-build.sh';builder.write_text(script)
    with (ROOT/'pipeline-four-module-cq-compile.log').open('wb') as log:
        code=subprocess.run(['bash',str(builder),str(source)],stdout=log,stderr=subprocess.STDOUT,timeout=300).returncode
    if code:return code
    report=json.loads((dest/'build-result.json').read_text())
    report.update(source_sha256=SOURCE_SHA,pipeline_build_variant='four-module-cq',compiler_scalar_dcci=False,
        stage_isolation_noinline=True,barrier_algorithm='leader_release',extra_round_fence=False,
        kernel_compatibility_variant='cq-memory-barrier',compatibility_header_sha256=comparison['overlay_header_sha256'],
        compatibility_baseline_header_sha256=comparison['baseline_header_sha256'],baseline_pipeline_object_sha256=baseline['sha256'])
    (dest/'build-result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,separators=(',',':')),flush=True)
    return 0

if __name__=='__main__':
    import sys
    raise SystemExit(main(sys.argv[1]))
