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
