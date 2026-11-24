# Copyright (c) 2026 TileXR Project.
"""Prepare two isolated timer waits using the same build and SST platform."""
import argparse,hashlib,json,shutil,struct,subprocess,sys
from pathlib import Path
import profile as ht_profile

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main(root,tools,wait_ticks):
    root,tools=Path(root),Path(tools);build=root/'clock-build-a1'
    if not build.exists():
        build.mkdir()
        original=json.loads((root/'profile-build/result.json').read_text())['targets']['local_ht']['commands']
        compile=list(original[0]);compile[compile.index('-c')+1]=str(tools/'clock_probe.cpp');compile[compile.index('-o')+1]=str(build/'clock.rel.o')
        compile.insert(1,'-I'+str(tools))
        link=list(original[1]);link[link.index('-Ttext=0')+1]=str(build/'clock.rel.o');link[link.index('-o')+1]=str(build/'kernel.o')
        for index,command in enumerate((compile,link)):
            with (build/('build-%d.log'%index)).open('wb') as f:p=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,timeout=180)
            assert p.returncode==0,'clock_build_failed'
        (build/'identity.json').write_text(json.dumps({'commands':[compile,link],'source_sha256':sha(tools/'clock_probe.cpp'),'object_sha256':sha(build/'kernel.o')},indent=2))
    sys.path.insert(0,str(root/'source/tools/unitest/simulator/experiments/inter_server_send/sst'))
    import loader,runtime
    case=root/'cases'/('clock-%d-a1'%wait_ticks);temporary=root/('clock-%d-preparation'%wait_ticks);temporary.mkdir(exist_ok=False)
    kernel=temporary/'kernel.o';raw=(build/'kernel.o').read_bytes();kernel.write_bytes(raw+bytes(10485760-len(raw)))
    specs=[]
    for rank in range(64):
        base=rank*runtime.GM_BYTES;out=base+0x440000000;prof=base+ht_profile.ADDRESS
        exports={name:dict(addr=out+offset,size=8) for name,offset in (('returned',0),('completed',8),('output',16),('status',24))}
        exports['profiling']=dict(addr=prof,size=ht_profile.layout(32)['bytes'])
        guards=[dict(name='guard_before',addr=out-512,size=512,expected_byte=222),dict(name='guard_after',addr=out+512,size=512,expected_byte=222)]
        specs.append(dict(rank=rank,abi_size=24,fields=[dict(name=name,offset=offset,type='uint64_t',value=value) for name,offset,value in (('result',0,out),('profiling',8,prof),('wait_ticks',16,wait_ticks))],
            gm_size=runtime.GM_BYTES,sys_va_base_addr=base,ub_base_addr=0x80000,stack_base_addr=0x100000,param_base_addr=base+0x80000000,binary_addr=base+0x40000000,
            images=[dict(name='result',addr=out,data=b'\xa5'*512),dict(name='profiling',addr=prof,data=ht_profile.image(prof,32)),dict(name='guard_before',addr=out-512,data=b'\xde'*512),dict(name='guard_after',addr=out+512,data=b'\xde'*512)],exports=exports,guards=guards))
    loader.prepare_loader(case,str(case),kernel,sha(kernel),'tilexr_ht_clock_probe',specs)
    workload=json.loads((case/'workload.yml').read_text())
    for row in workload['workload_config'].values():row['block_dim']=1
    (case/'workload.yml').write_text(json.dumps(workload,indent=2))
    layout=json.loads((case/'export-layout.json').read_text());layout['required_exports']=['returned','completed','output','status','profiling'];(case/'export-layout.json').write_text(json.dumps(layout,indent=2))
    fixture=case/'fixture-data';fixture.mkdir()
    meta=dict(world=64,tokens=4,stage='clock_probe',wait_ticks=wait_ticks,diagnostic_only=True,operator_qualified=False,
        profiling_capacity=32,blocks=1,routes_sha256='diagnostic_no_route',entry='tilexr_ht_clock_probe',kernel_sha256=sha(case/'kernel/inter_server_send.o'))
    (fixture/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(meta,separators=(',',':')))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--tools',required=True);p.add_argument('--wait-ticks',required=True,type=int);a=p.parse_args();main(a.root,a.tools,a.wait_ticks)
