# Copyright (c) 2026 TileXR Project.
"""Compile an identical inter-stage body in a private, non-inlined function frame."""
import hashlib,json,subprocess
from pathlib import Path

ROOT=Path('/work')

def main(tools):
    tools=Path(tools)
    target=ROOT/'pipeline-isolated-inter-a1'
    assert not target.exists()
    source=(tools/'pipeline_kernel.cpp').read_text()
    begin='    {\n        TPipe pipe;\n        InterStage::DeviceTransport transport('
    end='        JointStatus(pipe,args.sendStatus+uint64_t(block)*32,result);\n    }'
    assert source.count(begin)==source.count(end)==1
    first=source.index(begin);last=source.index(end,first)+len(end)
    body=source[first:last]
    entry='extern "C" __global__ __aicore__ void tilexr_ht_pipeline_sst(PipelineArgs args) {'
    assert source.count(entry)==1
    modified=source[:first]+'    JointInterStage(args,block);'+source[last:]
    helper='__aicore__ __attribute__((noinline)) void JointInterStage(const PipelineArgs &args,uint32_t block) {\n    using namespace AscendC;\n    using namespace TileXREpHt;\n'+body+'\n}\n\n'
    modified=modified.replace(entry,helper+entry)
    generated=ROOT/'pipeline-isolated-inter-source.cpp'
    assert not generated.exists();generated.write_text(modified)
    script=(tools/'pipeline_build.sh').read_text()
    assert script.count('target=/work/pipeline-build-40596e2-a1')==1
    assert script.count('-cce-aicore-dcci-insert-for-scalar=false')==1
    script=script.replace('target=/work/pipeline-build-40596e2-a1','target=/work/pipeline-isolated-inter-a1')
    # The generated source lives outside the script bundle; retain its header search path.
    script=script.replace('includes=("-I$(dirname "$1")"','includes=("-I'+str(tools)+'" "-I$(dirname "$1")"')
    builder=ROOT/'pipeline-isolated-inter-build.sh';builder.write_text(script)
    with (ROOT/'pipeline-isolated-inter-compile.log').open('wb') as log:
        result=subprocess.run(['bash',str(builder),str(generated)],stdout=log,stderr=subprocess.STDOUT,timeout=300)
    if result.returncode:return result.returncode
    report=json.loads((target/'build-result.json').read_text())
    symbols=subprocess.run(['readelf','-sW',str(target/'kernel.o')],capture_output=True,text=True,timeout=20)
    assert symbols.returncode==0
    helper_symbols=[line.strip() for line in symbols.stdout.splitlines() if 'JointInterStage' in line and 'FUNC' in line]
    assert helper_symbols,'inter_stage_was_not_kept_out_of_line'
    report.update(compiler_scalar_dcci=False,pipeline_build_variant='isolated-inter',stage_isolation_noinline=True,
        base_wrapper_sha256=hashlib.sha256(source.encode()).hexdigest(),source_sha256=hashlib.sha256(modified.encode()).hexdigest(),
        moved_inter_body_sha256=hashlib.sha256(body.encode()).hexdigest(),helper_symbols=helper_symbols)
    (target/'build-result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,separators=(',',':')),flush=True)
    return 0

if __name__=='__main__':
    import sys
    raise SystemExit(main(sys.argv[1]))
