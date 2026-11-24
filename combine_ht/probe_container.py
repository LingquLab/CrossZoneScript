"""Capture an SST crash without modifying candidate source or the failed case."""
import json,os,re,shutil,struct,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,'/work')
import sst
w=Path('/work')
a=json.loads(sys.argv[1])
result={'ok':True,'diagnostic_only':True,'action':a['action']}
if a['action']=='crash':
    case=w/'cases/8p-bs16-a1/send-only'
    out=w/'diagnostics/crash-a2'
    out.mkdir(parents=True,exist_ok=False)
    for name in ['v310_config','UBusSim_config']:
        shutil.copytree(case/name,out/name)
    for name in ['test_UBusSim.py','installed_model.py','environment.snapshot.json','workload.yml']:
        shutil.copyfile(case/name,out/name)
    with (out/'build.log').open('wb') as log:
        subprocess.run(['gcc','-shared','-fPIC','-O2','/work/sst_fault.c','-o',str(out/'fault.so')],check=True,stdout=log,stderr=subprocess.STDOUT,timeout=30)
    snapshot=json.loads((w/'environment.snapshot.json').read_text())
    env=snapshot['settings']
    command=sst.shell_command(env,'sst',['env','LD_PRELOAD='+str(out/'fault.so'),'TILEXR_SST_FAULT_FILE='+str(out/'fault.txt'),env['sst_bin'],'-n','16',out/'test_UBusSim.py'])
    start=time.monotonic()
    with (out/'sim.log').open('wb') as log:
        p=subprocess.run(command,cwd=out,stdout=log,stderr=subprocess.STDOUT,timeout=a.get('timeout',240))
    result.update(simulator_exit=p.returncode,wall_s=time.monotonic()-start,report=str(out),kernel_sha256=sst.checksum(sst.KERNEL))
    lines=(out/'fault.txt').read_text().splitlines()
    assert len(lines)>=3,'no_crash_register_capture'
    addresses=[int(x,16) for x in lines[:3]]
    result['fault_address']=hex(addresses[2])
    result['frames']=[]
    for label,address in zip(['pc','lr'],addresses[:2]):
        for line in lines[3:]:
            m=re.match(r'([0-9a-f]+)-([0-9a-f]+)\s+\S+\s+([0-9a-f]+)\s+\S+\s+\d+\s+(.+)',line)
            if m and int(m[1],16)<=address<int(m[2],16):
                path=m[4].strip();offset=address-int(m[1],16)+int(m[3],16)
                frame={'label':label,'library':path,'offset':hex(offset)}
                tool=shutil.which('addr2line')
                if tool:
                    decoded=subprocess.run([tool,'-f','-C','-e',path,hex(offset)],capture_output=True,text=True,timeout=15)
                    frame['symbol']=decoded.stdout.strip()[:700]
                result['frames'].append(frame);break
    text=(out/'sim.log').read_bytes().decode(errors='replace')
    result['fatal_lines']=[x[:500] for x in text.splitlines() if re.search(r'FATAL:|Unknown low level|Assertion',x)][:4]
    result['ok']=len(result['frames'])==2
elif a['action']=='mmio':
    result['headers']=[]
    roots=[Path('/opt/sim/Ascend/cann-9.1.0/tools/bisheng_compiler/lib/clang/15.0.5/include'),Path('/opt/sim/Ascend/cann-9.1.0/aarch64-linux/ascendc/include')]
    for root in roots:
        for path in root.rglob('*.h'):
            text=path.read_text(errors='replace')
            for line in text.splitlines():
                if re.search(r'\bst_dev\b|\basc_store_dev\b',line) and len(result['headers'])<8:
                    result['headers'].append({'path':str(path),'line':line.strip()[:400]})
    library=Path('/opt/sim/plugins/libDavinciSimulatorV310.so')
    symbols=subprocess.run(['nm','-D','-S','-C',str(library)],capture_output=True,text=True,check=True,timeout=25).stdout
    folder=w/'diagnostics/mmio-current';folder.mkdir(exist_ok=False)
    result['methods']=[]
    for line in symbols.splitlines():
        if '::ScalarStDev::' not in line or not any(x+'(' in line for x in ['decode','execute','issue1Rd']): continue
        words=line.split(maxsplit=3);start=int(words[0],16);size=int(words[1],16)
        asm=subprocess.run(['objdump','-d','-C','--start-address='+str(start),'--stop-address='+str(start+size),str(library)],capture_output=True,text=True,check=True,timeout=25).stdout
        (folder/(words[0]+'.asm')).write_text(asm)
        instructions=[x.strip() for x in asm.splitlines() if re.search(r'\blsl\b|\blsr\b|\bubfx\b|\bubfiz\b',x)]
        calls=[x.split('<',1)[1].rsplit('>',1)[0][:200] for x in asm.splitlines() if re.search(r'\bbl\b',x) and '<' in x]
        result['methods'].append(dict(name=words[3],address=hex(start),bytes=size,shifts=instructions[:3],calls=calls[:10]))
    case=w/'cases/8p-bs16-29f973b0-a1/send-only'
    import yaml
    config=yaml.safe_load((case/'v310_config/davidV100_simulator_config.yml').read_text())
    result['su']=config['simulator_config']['su']
    result['gdb']=shutil.which('gdb')
    # Match the exact fatal to a queue in the actual serialized inputs.
    raw=(case/'input_bin/rank3/sq_ctx.bin').read_bytes()
    target=563912026117120
    result['queue_matches']=[]
    for flat in range(len(raw)//80):
        address=struct.unpack_from('<Q',raw,flat*80+48)[0]
        if address*8==target: result['queue_matches'].append(dict(flat=flat,doorbell=hex(address),fatal=hex(target),factor=8))
elif a['action'] in ('stdev-gm','stdev-trace'):
    tracing=a['action']=='stdev-trace'
    folder=w/('diagnostics/stdev-trace-a1' if tracing else 'diagnostics/stdev-gm-a1');folder.mkdir(exist_ok=False)
    config=json.loads((w/'environment.snapshot.json').read_text())
    binary=folder/'probe.o'
    if tracing:
        shutil.copyfile(w/'diagnostics/stdev-gm-a1/probe.o',binary)
        assert sst.checksum(binary)=='6ccd3716098e081a1a9f61e4152c1076489c4ab47cc4f2afbc1ecdaff4fdf21e'
        configure=sst.configure
        def trace_config(sources,args):
            args.instruction_dump_chips=[0]
            return configure(sources,args)
        sst.configure=trace_config
    else:
        with (folder/'build.log').open('wb') as log:
            sst.build_kernel(config['settings'],log,source=w/'stdev_probe.cpp',binary=binary)
    initial=folder/'poison.bin';initial.write_bytes(bytes([0xcc])*32768)
    workload={'workload_config':{}}
    for rank in range(8):
        base=rank*sst.rt.GM_BYTES;target=base+0x200000
        params={'dst':dict(value=target,param_offset='0',param_type='11'),'rank':dict(value=rank,param_offset='8',param_type='5')}
        outputs={}
        if rank==0:
            for index,address in enumerate([target,target*8]):
                params['load'+str(index)]=dict(value=address,param_offset=str(16+8*index),param_type='11',input_bin=str(initial))
                outputs['region'+str(index)]=dict(addr=address,size=32768)
        workload['workload_config']['chip'+str(rank)]=dict(gm_size=sst.rt.GM_BYTES,sys_va_base_addr=base,ub_base_addr=0x80000,stack_base_addr=0x100000,param_base_addr=base+0x80000000,block_dim=1,subblock_dim=1,kernel_file=str(binary),vec_func=sst.ENTRY,kernel_param=params,bin_param=dict(addr=base+0x40000000,size=max(0x26c40,sst.fixtures.align(binary.stat().st_size))),output_param=outputs)
    with (folder/'model.log').open('w') as log,sst.redirect_stdout(log):
        model=sst.write_model(folder,config,workload,8)
    command=sst.shell_command(config['settings'],'sst',[config['settings']['sst_bin'],'-n','32',folder/'test_UBusSim.py'])
    start=time.monotonic()
    with (folder/'sim.log').open('wb') as log:
        process=subprocess.run(command,cwd=folder,stdout=log,stderr=subprocess.STDOUT,timeout=120)
    result.update(simulator_exit=process.returncode,wall_s=time.monotonic()-start,report=str(folder),probe_sha256=sst.checksum(binary))
    text=(folder/'sim.log').read_bytes().decode(errors='replace')
    result['fatals']=[x[:500] for x in text.splitlines() if 'FATAL:' in x][:3]
    result['completions']=[m.group(0) for m in sst.SIM_TIME.finditer(text)][-2:]
    result['regions']=[]
    if process.returncode==0:
        for index,address in enumerate([0x200000,0x1000000]):
            data=sst.read_output(folder,0,'region'+str(index),address,32768)
            changed=[i for i,value in enumerate(data) if value!=0xcc]
            result['regions'].append(dict(address=hex(address),changed_bytes=len(changed),first_changes=[dict(offset=i,value=data[i]) for i in changed[:24]],samples={str(i):data[i:i+8].hex() for i in [0,512,1024,1536,4096,8192,12288]}))
    if tracing:
        header=Path('/opt/sim/Ascend/cann-9.1.0/aarch64-linux/ascendc/include/basic_api/impl/kernel_scalar.h')
        lines=header.read_text().splitlines()
        hit=next(i for i,line in enumerate(lines) if 'st_dev' in line and 'uint32_t' in line)
        result['sdk_store_context']=lines[max(0,hit-4):hit+4]
        result['instruction_records']=[]
        for path in sorted(folder.rglob('*sudetail.dump')):
            previous=[]
            for line in path.read_text(errors='replace').splitlines():
                if 'scalarStDev' in line and len(result['instruction_records'])<3:
                    result['instruction_records'].append(dict(path=str(path),context=previous[-2:]+[line.strip()[:500]]))
                if any(x in line for x in ['scalarLdImm','scalarStDev','rdGpr','write x','Value:','value=']):
                    previous.append(line.strip()[:500])
            if len(result['instruction_records'])>=3:break
        paths=list((w/'diagnostics/mmio-current').glob('*4b3504.asm'))
        if paths:
            lines=[line.strip() for line in paths[0].read_text().splitlines() if re.match(r'\s*[0-9a-f]+:',line)]
            index=next(i for i,line in enumerate(lines) if re.search(r'\blsl\b',line))
            result['model_shift_context']=lines[max(0,index-4):index+3]
    result['ok']=bool(result['completions']) and process.returncode==0
elif a['action']=='stdev-ir':
    folder=w/'diagnostics/stdev-ir-a1';folder.mkdir(exist_ok=False)
    original=json.loads((w/'diagnostics/stdev-gm-a1/build.json').read_text())[0]
    config=json.loads((w/'environment.snapshot.json').read_text())
    bitcode=folder/'probe.bc';decoded=folder/'decoded.ll'
    command=original[:]
    command.insert(1,'-emit-llvm')
    command[command.index('-o')+1]=str(bitcode)
    with (folder/'compile.log').open('wb') as log:
        compiled=subprocess.run(sst.shell_command(config['settings'],'cann',command),stdout=log,stderr=subprocess.STDOUT,timeout=120)
    result['compile_exit']=compiled.returncode
    assert compiled.returncode==0 and bitcode.is_file(),'bitcode_not_emitted'
    assert bitcode.read_bytes()[:4]==b'BC\xc0\xde','not_llvm_bitcode'
    command=[original[0],'-cc1','-triple','hiipu64-hisilicon-cce','-x','ir','-emit-llvm',str(bitcode),'-o',str(decoded)]
    with (folder/'decode.log').open('wb') as log:
        proc=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=90)
    assert proc.returncode==0 and decoded.is_file(),'bitcode_decode_failed'
    lines=decoded.read_text().splitlines()
    hits=[line.strip() for line in lines if 'call ' in line and re.search(r'st[._]dev|store.*dev',line,re.I)]
    result.update(report=str(folder),bitcode_sha256=sst.checksum(bitcode),call_count=len(hits),calls=hits[:8])
    result['declarations']=[line.strip() for line in lines if line.startswith('declare ') and re.search(r'st[._]dev|store.*dev',line,re.I)][:6]
    result['nearby_address_operations']=[]
    for index,line in enumerate(lines):
        if line.strip() in hits:
            result['nearby_address_operations'].extend([x.strip() for x in lines[max(0,index-3):index] if 'getelementptr' in x or 'ptrtoint' in x or 'shl' in x or 'lshr' in x])
elif a['action']=='read':
    rel=a.get('report','cases/8p-bs16-a1/send-only')
    assert re.fullmatch(r'[\w/-]+',rel) and '..' not in rel
    case=w/rel
    if (case/'run.json').exists():
        saved=json.loads((case/'run.json').read_text())
        saved.pop('model',None)
        result['run']=saved
    for name in ['sim.log','build.log']:
        path=case/name
        if path.exists():
            data=path.read_bytes()
            result[name]={'bytes':len(data),'errors':[x[:500] for x in data.decode(errors='replace').splitlines() if re.search(r'FATAL:|Unknown low level|Assertion|Error:|error:|failed',x)][:8]}
else:
    raise ValueError('unknown_action')
(w/('diagnostic-'+a['action']+'.json')).write_text(json.dumps(result,indent=2))
print(json.dumps(result))
