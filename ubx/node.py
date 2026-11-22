"""Run the built TRACE fixture and retain the two rounds on each physical host."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import time

root = Path(SPEC['root'])
run = root / SPEC['run']
binary = root / 'host-TRACE/combine_push'
lib = root / 'install-TRACE/lib'
node = SPEC['node']
assert socket.gethostname() == ('root-84', 'root-184')[node]

def occupancy(i):
    x = subprocess.run(['timeout','8','npu-smi','info','-t','proc-mem','-i',str(i)], capture_output=True)
    if x.returncode or not re.search(rb'NPU ID\s*:\s*'+str(i).encode()+rb'(?:\s|$)', x.stdout):
        return 'unknown'
    return 'busy' if re.search(rb'Process id\s*:', x.stdout, re.I) else 'idle'

def sample():
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(occupancy, range(16)))

def prepare():
    m=json.loads((root/'build-result.json').read_text())
    assert m['status']=='BUILD_READY' and m['revision']==SPEC['revision']
    for name, expected in SPEC['hashes'].items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected, name
    assert not run.exists()
    assert shutil.disk_usage(root).free > 6*1024**3
    envdata=subprocess.check_output(['bash','-c','. /usr/local/Ascend/cann-9.2.T101/set_env.sh >/dev/null && env -0'],timeout=20)
    env=dict(p.decode().split('=',1) for p in envdata.split(b'\0') if b'=' in p)
    env={k:v for k,v in env.items() if not k.startswith(('TILEXR_','HCCL_')) and k not in ('ASCEND_VISIBLE_DEVICES','ASCEND_RT_VISIBLE_DEVICES')}
    paths=[str(lib),'/usr/local/Ascend/driver/lib64/driver','/usr/local/Ascend/driver/lib64/common']+env.get('LD_LIBRARY_PATH','').split(':')
    env['LD_LIBRARY_PATH']=':'.join(dict.fromkeys(p for p in paths if p and not any(v in p for v in ('/devlib','/simulator','/stub'))))
    deps=subprocess.check_output(['ldd',str(binary)],env=env,text=True,timeout=15)
    assert 'not found' not in deps and all(str(lib/name) in deps for name in ['libtile-comm.so','libtilexr-ep.so','libtilexr-diagnostics.so'])
    return env

def execute():
    env=prepare()
    states=sample()
    if states!=['idle']*16:
        return dict(status='CAPACITY_CHANGED',devices=states,started=0)
    run.mkdir()
    (run/'request.json').write_text(json.dumps(SPEC,indent=2))
    children=[]
    try:
        for device in range(16):
            rank=node*16+device
            d=run/('rank-'+str(rank))
            d.mkdir()
            penv=dict(env,RANK_SIZE='32',RANK=str(rank),DEVICE_ID=str(device),TILEXR_COMM_ID='90.90.93.84:'+str(SPEC['port']-1),TILEXR_IPC_SCOPE='local',TILEXR_ENABLE_IPC='1',TILEXR_ENABLE_CREDIT_IPC='0',TILEXR_ENABLE_RDMA='1',TILEXR_ENABLE_UDMA='0',TILEXR_ENABLE_SDMA='0',TILEXR_RDMA_MODE='aiv',TILEXR_RDMA_DMA_MODE='indep_ub',TILEXR_RDMA_DB_MODE='hard',TILEXR_RDMA_DEVICE='hrn5_'+str(device//2),TILEXR_RDMA_GID_INDEX='1',TILEXR_COMBINE_CLOCK='sleep',ASCEND_PROCESS_LOG_PATH=str(d/'plog'))
            with (d/'launcher.log').open('wb') as log:
                p=subprocess.Popen([str(binary),'mixed','32','8','2','1','30000000000','32768',str(d),'pid'],env=penv,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            children.append(p)
        deadline=time.monotonic()+420
        while time.monotonic()<deadline and any(p.poll() is None for p in children):
            if any(p.poll() not in (0,None) for p in children):break
            time.sleep(.25)
    finally:
        for p in children:
            if p.poll() is None:p.terminate()
        for p in children:
            try:p.wait(timeout=3)
            except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
    exits=[p.returncode for p in children]
    receipts=list(run.glob('rank-*/*.receipt.json'))
    correct=sum(json.loads(p.read_text())['correctness']=='PASS' for p in receipts)
    ok=exits==[0]*16 and correct==32 and len(list(run.glob('rank-*/*.trace.bin')))==32 and len(list(run.glob('rank-*/*.clock.json')))==32
    errors=[]
    for f in sorted(run.glob('rank-*/launcher.log')):
        lines=[s[-280:] for s in f.read_text(errors='replace').splitlines() if 'COMBINE_FAIL' in s or 'ERROR' in s]
        if lines and len(errors)<4:errors.append([f.parent.name,lines[-2:]])
    result=dict(status='NODE_PASS' if ok else 'NODE_FAIL',started=len(children),exits=exits,correct_receipts=correct,errors=errors,path=str(run))
    (run/'result.json').write_text(json.dumps(result,indent=2))
    return result

if SPEC['action']=='prepare':
    prepare()
    print(json.dumps({'ready':True}))
elif SPEC['action']=='sample':
    with socket.socket() as s:
        s.bind(('',0))
        port=s.getsockname()[1]
    print(json.dumps(dict(devices=sample(),port=port)))
else:
    print(json.dumps(execute()))
