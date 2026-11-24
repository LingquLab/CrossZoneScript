# Copyright (c) 2026 TileXR Project.
"""Compile an identical inter-stage body in a private, non-inlined function frame."""
import hashlib,json,subprocess
from pathlib import Path

ROOT=Path('/work')

def main(tools,variant='isolated-inter'):
    tools=Path(tools)
    assert variant in ('isolated-inter','leader-barrier')
    prefix='pipeline-'+variant
    target=ROOT/(prefix+'-a1')
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
    if variant=='leader-barrier':
        first=modified.index('__aicore__ bool JointBarrier(')
        last=modified.index('__aicore__ void JointCopyPartial(',first)
        modified=modified[:first]+(tools/'leader_barrier.h').read_text()+'\n'+modified[last:]
    generated=ROOT/(prefix+'-source.cpp')
    assert not generated.exists();generated.write_text(modified)
    script=(tools/'pipeline_build.sh').read_text()
    assert script.count('target=/work/pipeline-build-40596e2-a1')==1
    assert script.count('-cce-aicore-dcci-insert-for-scalar=false')==1
    script=script.replace('target=/work/pipeline-build-40596e2-a1','target='+str(target))
    # The generated source lives outside the script bundle; retain its header search path.
    script=script.replace('includes=("-I$(dirname "$1")"','includes=("-I'+str(tools)+'" "-I$(dirname "$1")"')
    builder=ROOT/(prefix+'-build.sh');builder.write_text(script)
    with (ROOT/(prefix+'-compile.log')).open('wb') as log:
        result=subprocess.run(['bash',str(builder),str(generated)],stdout=log,stderr=subprocess.STDOUT,timeout=300)
    if result.returncode:return result.returncode
    report=json.loads((target/'build-result.json').read_text())
    symbols=subprocess.run(['readelf','-sW',str(target/'kernel.o')],capture_output=True,text=True,timeout=20)
    assert symbols.returncode==0
    helper_symbols=[line.strip() for line in symbols.stdout.splitlines() if 'JointInterStage' in line and 'FUNC' in line]
    assert helper_symbols,'inter_stage_was_not_kept_out_of_line'
    report.update(compiler_scalar_dcci=False,pipeline_build_variant=variant,stage_isolation_noinline=True,
        barrier_algorithm='leader_release' if variant=='leader-barrier' else 'all_lanes_poll_all',
        base_wrapper_sha256=hashlib.sha256(source.encode()).hexdigest(),source_sha256=hashlib.sha256(modified.encode()).hexdigest(),
        moved_inter_body_sha256=hashlib.sha256(body.encode()).hexdigest(),helper_symbols=helper_symbols)
    (target/'build-result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,separators=(',',':')),flush=True)
    return 0

if __name__=='__main__':
    import sys
    raise SystemExit(main(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else 'isolated-inter'))
