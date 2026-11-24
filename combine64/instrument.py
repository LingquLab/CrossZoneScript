# Copyright (c) 2026 TileXR Project.
"""Add shared profiling markers around immutable return-stage entry bodies."""
import hashlib,json,subprocess,re
from pathlib import Path

def main(root):
    root=Path(root);source=root/'source';dest=root/'profile-build';dest.mkdir(exist_ok=False)
    cann=Path('/opt/sim/Ascend/cann-9.1.0')
    include=['-I'+str(source/'include'),'-I'+str(source/'src/ep/combine_ht'),
        '-I'+str(source/'src/moonep/combine_ht/common'),'-I'+str(source/'src/moonep/combine_ht/kernels'),'-I'+str(Path(__file__).parent)]
    for rel in ('compiler/tikcpp','compiler/tikcpp/tikcfw','compiler/tikcpp/tikcfw/impl','compiler/tikcpp/tikcfw/interface',
                'aarch64-linux/ascendc/include','aarch64-linux/asc/include','aarch64-linux/include','aarch64-linux/pkg_inc','aarch64-linux/pkg_inc/runtime'):
        include+=['-I'+str(cann/rel)]
    flags=['-xcce','-O2','-DCATLASS_ARCH=3510','--cce-auto-sync','-Wno-deprecated-declarations',
        '--cce-aicore-arch=dav-c310-vec','-mllvm','-cce-aicore-stack-size=0x8000',
        '-mllvm','-cce-aicore-function-stack-size=0x8000','-mllvm','-cce-aicore-addr-transform',
        '-mllvm','-cce-aicore-dcci-insert-for-scalar=false','-DTILEXR_COMBINE_HT_SIM_SEND=1',
        '-DTILEXR_COMBINE_HT_SST_FP32=1','-DTILEXR_EP_HT_SST_SPLIT_WRITES=1',
        '-DTILEXR_EP_HT_FINAL_REDUCE_SST_FP32=1','-DTILEXR_EP_HT_PIPELINE_FP32=1',
        '-DTILEXR_ENABLE_PROFILING=1','--cce-aicore-only','-std=gnu++17']+include
    targets={}
    for stage in ('local_ht','local_return','inter_server_send','final_reduce'):
        origin=source/('src/moonep/combine_ht/kernels/combine_ht.cpp' if stage=='local_ht' else 'src/ep/combine_ht/'+stage+'_kernel.cpp');text=origin.read_text()
        # Only the exported compute entry's signature and prologue/epilogue change.
        start=text.rindex('extern "C" __global__ __aicore__ void ')
        # The final entry has a conditional symbol name; its argument list is shared.
        opening=text.index('\n{',start);closing=text.rindex('\n}')
        signature=text[start:opening]
        assert signature.rstrip().endswith(')')
        signature=signature.rstrip()[:-1]+', GM_ADDR profiling)'
        body=text[opening+2:closing]
        prologue='\n    TileXR::Profiling::DirectWriter capture;\n    capture.Initialize(profiling, AscendC::GetBlockIdx());\n    capture.RecordAt(1, AscendC::GetSystemCycle());\n'
        epilogue='\n    capture.RecordAt(2, AscendC::GetSystemCycle());\n    capture.Publish();\n    FlushProfile(profiling);\n'
        code='#include <tilexr/profiling/device/direct_writer.h>\n#include \"profile_flush.h\"\n'+text[:start]+signature+'\n{'+prologue+body+epilogue+text[closing:]
        if stage=='local_ht':
            code='#include \"combine_ht_kernel.h\"\n#include \"profile_flush.h\"\nextern \"C\" __global__ __aicore__ void tilexr_moonep_combine_ht_kernel(TileXRCombineHT::KernelArgs args) { RunCombineHT(args); FlushProfile(args.profiling); }\n'
        wrapper=dest/(stage+'.cpp');wrapper.write_text(code)
        obj=dest/(stage+'.rel.o');binary=dest/(stage+'.o')
        commands=[['bisheng']+flags+['-c',str(wrapper),'-o',str(obj)],
                  ['ld.lld','-m','aicorelinux','-Ttext=0',str(obj),'--static','--allow-multiple-definition','-o',str(binary)]]
        statuses=[]
        for i,cmd in enumerate(commands):
            with (dest/(stage+'-'+str(i)+'.log')).open('wb') as log:p=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=180)
            statuses.append(p.returncode)
            if p.returncode:break
        row=dict(exit_codes=statuses,original_sha256=hashlib.sha256(origin.read_bytes()).hexdigest(),
                 instrumentation_only=True,path=str(binary.relative_to(root)),commands=commands)
        if binary.is_file():row['sha256']=hashlib.sha256(binary.read_bytes()).hexdigest()
        if statuses!=[0,0]:
            row['errors']=[l[:240] for l in (dest/(stage+'-'+str(len(statuses)-1)+'.log')).read_text(errors='replace').splitlines() if 'error:' in l][:4]
        targets[stage]=row
    result=dict(all_compiled=all(x['exit_codes']==[0,0] for x in targets.values()),targets=targets)
    (dest/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,separators=(',',':')))
    return 0 if result['all_compiled'] else 1
if __name__=='__main__':
    import sys
    raise SystemExit(main(sys.argv[1]))
