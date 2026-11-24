"""Capture an SST crash without modifying candidate source or the failed case."""
import json,os,re,shutil,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,'/work')
import sst
w=Path('/work')
a=json.loads(sys.argv[1])
result={'ok':True,'diagnostic_only':True,'action':a['action']}
if a['action']=='crash':
    case=w/'cases/8p-bs16-a1/send-only'
    out=w/'diagnostics/crash-a1'
    out.mkdir(parents=True,exist_ok=False)
    for name in ['v310_config','UBusSim_config']:
        shutil.copytree(case/name,out/name)
    for name in ['test_UBusSim.py','installed_model.py']:
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
