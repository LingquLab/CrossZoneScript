# Copyright (c) 2026 TileXR Project.
"""Build and run a bounded GM-access comparison without changing candidate code."""
import hashlib,importlib.util,json,os,re,shutil,signal,struct,subprocess,sys,time
from pathlib import Path

ROOT=Path('/work')
ENTRY='tilexr_queue_access_probe'
TARGET=ROOT/'queue-access-probe-a1'

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    tools=Path(sys.argv[1])
    TARGET.mkdir(exist_ok=False)
    assert json.loads((ROOT/'identity.json').read_text())['revision']=='013cc3e2f8abf8157abdc609afcfe2369806b1e1'
    for process in Path('/proc').glob('[0-9]*/comm'):
        try:assert not process.read_text().strip().startswith('sstsim'),'another_sst_is_running'
        except OSError:pass
    cann=Path('/opt/sim/Ascend/cann-9.1.0')
    compiler=cann/'aarch64-linux/ccec_compiler/bin/bisheng'
    if not compiler.exists():compiler=Path(shutil.which('bisheng'))
    command=[str(compiler),'-xcce','-O2','-DCATLASS_ARCH=3510','--cce-auto-sync','-Wno-deprecated-declarations',
             '--cce-aicore-arch=dav-c310-vec','-mllvm','-cce-aicore-stack-size=0x8000',
             '-mllvm','-cce-aicore-function-stack-size=0x8000','-mllvm','-cce-aicore-addr-transform',
             '-mllvm','-cce-aicore-dcci-insert-for-scalar=false','--cce-aicore-only','-std=gnu++17']
    for name in ('compiler/tikcpp','compiler/tikcpp/tikcfw','compiler/tikcpp/tikcfw/impl','compiler/tikcpp/tikcfw/interface',
                 'aarch64-linux/ascendc/include','aarch64-linux/asc/include','aarch64-linux/include','aarch64-linux/pkg_inc','aarch64-linux/pkg_inc/runtime'):
        command+=['-I'+str(cann/name)]
    command+=['-c',str(tools/'queue_access_probe.cpp'),'-o',str(TARGET/'rel.o')]
    result={'diagnostic_only':True,'operator_qualified':False,'source_sha256':digest(tools/'queue_access_probe.cpp')}
    report=TARGET/'result.json'
    with (TARGET/'compile.log').open('wb') as log:
        p=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=90)
    result['compile_exit']=p.returncode
    if p.returncode:
        result['errors']=[s[:300] for s in (TARGET/'compile.log').read_text(errors='replace').splitlines() if 'error:' in s][:5]
        report.write_text(json.dumps(result,indent=2));print(json.dumps(result));return 1
    linker=compiler.with_name('ld.lld')
    if not linker.exists():linker=Path(shutil.which('ld.lld'))
    with (TARGET/'link.log').open('wb') as log:
        p=subprocess.run([str(linker),'-m','aicorelinux','-Ttext=0',str(TARGET/'rel.o'),'--static','--allow-multiple-definition','-o',str(TARGET/'kernel.o')],stdout=log,stderr=subprocess.STDOUT,timeout=30)
    result['link_exit']=p.returncode
    assert p.returncode==0,'probe_link_failed'
    result['kernel_sha256']=digest(TARGET/'kernel.o')
    support=ROOT/'source/tools/unitest/simulator/experiments/inter_server_send/sst'
    sys.path.insert(0,str(support));import loader
    source=ROOT/'cases/64p-t4-final-independent-a1'
    original=json.loads((source/'workload.yml').read_text())['workload_config']
    ranks=[]
    for rank in range(64):
        chip=original['chip%d'%rank];base=rank*0x2000000000
        data=bytearray(4096)
        for cell in range(8):struct.pack_into('<I',data,cell*512,0x12340000+rank*16+cell)
        ranks.append(dict(rank=rank,abi_size=16,fields=[dict(name='counters',offset=0,type='GM_ADDR',value=base+0x1b00000000),
            dict(name='output',offset=8,type='GM_ADDR',value=base+0x1b00010000)],gm_size=chip['gm_size'],sys_va_base_addr=base,
            ub_base_addr=chip['ub_base_addr'],stack_base_addr=chip['stack_base_addr'],param_base_addr=chip['param_base_addr'],
            binary_addr=chip['bin_param']['addr'],images=[dict(name='counters',addr=base+0x1b00000000,data=bytes(data)),
                dict(name='output',addr=base+0x1b00010000,data=b'\xa5'*512)],
            exports={'output':{'addr':base+0x1b00010000,'size':512}}))
    raw=(TARGET/'kernel.o').read_bytes();padded=TARGET/'padded.o';padded.write_bytes(raw+bytes(10485760-len(raw)))
    case=TARGET/'case'
    loader.prepare_loader(case,str(case),padded,digest(padded),ENTRY,ranks)
    platform=json.loads((source/'case-manifest.json').read_text())
    skip={'workload.yml','export-layout.json','case-manifest.json','blue-build-record.json','loader-manifest.json',
          'runtime-layout.json','blue-case-identity.json','v310_config/workload_config.yml'}
    for name,expected in platform['files'].items():
        relative=Path(name)
        assert not relative.is_absolute() and '..' not in relative.parts
        if relative.parts[0] in ('input_bin','fixture-data','kernel') or name in skip:continue
        src,dst=source/relative,case/relative
        assert digest(src)==expected,'platform_file_changed'
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,dst)
    assert (case/'environment.snapshot.json').is_file()
    workload=json.loads((case/'workload.yml').read_text())
    for chip in workload['workload_config'].values():chip['block_dim']=1
    (case/'workload.yml').write_text(json.dumps(workload,indent=2))
    shutil.copyfile(case/'workload.yml',case/'v310_config/workload_config.yml')
    env=json.loads((case/'sst-environment.json').read_text())
    command=['bash','-c','set -e; source "$1"; shift; exec "$@"','bash',env['env_script'],env['sst_bin'],'-n','8','--stop-at=100us',str(case/'test_UBusSim.py')]
    start=time.monotonic()
    with (case/'sim.log').open('wb') as log:
        child=subprocess.Popen(command,cwd=case,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:code=child.wait(timeout=150)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid,signal.SIGTERM)
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=10)
            code=child.returncode;result['wall_timeout']=True
    result.update(sst_exit=code,wall_seconds=time.monotonic()-start)
    report.write_text(json.dumps(result,indent=2))
    assert code==0 and not result.get('wall_timeout'),'probe_sst_failed'
    spec=importlib.util.spec_from_file_location('saved_exports',source/'adapter/exports.py')
    exports=importlib.util.module_from_spec(spec);spec.loader.exec_module(exports)
    mismatches=[];matched=[0]*8;all_rows=[]
    for rank in range(64):
        payload=exports.reconstruct_die_output([case/'bin'/('david_%d_die%d_output.bin'%(rank,d)) for d in (1,2)],0x1b00010000,512,env['decoder_library'])
        for cell in range(8):
            initial=0x12340000+rank*16+cell
            expected=[initial,initial,initial+17,initial+17,initial+33,initial+33,initial+41,initial+41]
            actual=list(struct.unpack_from('<8I',payload,cell*32))
            for i,(a,b) in enumerate(zip(actual,expected)):matched[i]+=a==b
            row=dict(rank=rank,cell=cell,expected=expected,actual=actual);all_rows.append(row)
            if actual!=expected:mismatches.append(row)
    result.update(comparisons_per_column=512,matched_columns=matched,mismatch_cells=len(mismatches),examples=mismatches[:6],diagnostic_finished=matched[1]==matched[5]==512)
    report.write_text(json.dumps(dict(result,all_rows=all_rows),indent=2))
    print(json.dumps(result,separators=(',',':')))
    return 0 if result['diagnostic_finished'] else 1
if __name__=='__main__':raise SystemExit(main())
