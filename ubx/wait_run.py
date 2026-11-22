"""Monitor both servers, run one EP32 test and merge the captured rounds in green."""
import concurrent.futures
import datetime
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
from common import HOSTS,ROOT,REMOTE_ROOT,REVISION,remote_python,json_rows,ssh

run='trace-mixed-align-002'
dest=ROOT/run
dest.mkdir(exist_ok=False)
manifest=json.loads((ROOT/'ep32-build-result.json').read_text())
hashes=manifest['hosts'][0]['rows'][0]['artifacts']
assert all(h['rows'][0]['artifacts']==hashes for h in manifest['hosts'])
script=Path(__file__).with_name('node.py').read_text(encoding='utf-8')
def both(action,port=0):
    def one(node):
        spec=dict(action=action,root=REMOTE_ROOT,revision=REVISION,hashes=hashes,node=node,run=run,port=port)
        r=remote_python(HOSTS[node],'SPEC='+repr(spec)+'\n'+script,timeout=500 if action=='run' else 55,label='ep32-'+action)
        rows=json_rows(r.stdout)
        return rows[-1] if r.returncode==0 and rows else {'status':'REMOTE_ERROR','exit':r.returncode}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(one,range(2)))

def main():
    assert all(v.get('ready') for v in both('prepare'))
    deadline=time.monotonic()+6*3600
    with (dest/'occupancy.jsonl').open('x') as log:
        while time.monotonic()<deadline:
            rows=both('sample')
            log.write(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'hosts':rows})+'\n')
            log.flush()
            if all(v.get('devices')==['idle']*16 for v in rows):
                result=both('run',rows[0]['port'])
                if all(v.get('status')=='CAPACITY_CHANGED' for v in result):continue
                break
            time.sleep(12)
        else:raise TimeoutError('No full EP32 idle window during six hours')
    (dest/'nodes.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({'nodes':result,'directory':str(dest)}),flush=True)
    assert all(v.get('status')=='NODE_PASS' for v in result)
    source=ROOT/('source-'+REVISION[:8])
    mapping='src/ep/profiling/ubx_combine_push.mapping.json'
    identity=dict(source_sha=REVISION,host_sha256=hashes['host-TRACE/combine_push'],kernel_sha256=hashes['install-TRACE/lib/tilexr/kernels/tilexr_ep_ubx_combine_kernel.o'],mapping_sha256=hashes['source/'+mapping],mode='TRACE')
    (dest/'identity.json').write_text(json.dumps(identity))
    assert shutil.disk_usage(dest).free>10*1024**3
    for node,host in enumerate(HOSTS):
        # Transfer compressed capture data within green. Keep bulky original records on the test hosts.
        path=REMOTE_ROOT+'/'+run
        for rank in range(node*16,node*16+16):
            r=ssh(host,['tar','-czf','-','-C',path,'--wildcards','--exclude=*.log','--exclude=plog','rank-'+str(rank)],timeout=120,label='ep32-copy-rank-'+str(rank))
            r.check_returncode()
            with tarfile.open(fileobj=io.BytesIO(r.stdout),mode='r:gz') as tar:
                for m in tar:
                    if m.isdir():continue
                    assert m.isfile() and re.fullmatch('rank-'+str(rank)+'/rank-'+str(rank)+r'-round-[01]\.(receipt\.json|clock\.json|trace\.bin|results\.bin)',m.name)
                    with (dest/Path(m.name).name).open('xb') as f:shutil.copyfileobj(tar.extractfile(m),f)
    for i in range(2):
        receipts=sorted(dest.glob('rank-*-round-'+str(i)+'.receipt.json'))
        assert len(receipts)==32
        out=dest/('round-'+str(i)+'.perfetto.json')
        with (dest/('merge-'+str(i)+'.log')).open('wb') as log:
            subprocess.run([sys.executable,str(source/'tools/profiling/ubx_combine_trace.py'),'--mapping',str(source/mapping),'--identity',str(dest/'identity.json'),'--receipts',*map(str,receipts),'--output',str(out)],check=True,timeout=900,stdout=log,stderr=log)
        print(json.dumps({'perfetto':str(out),'bytes':out.stat().st_size,'ranks':len(receipts)}),flush=True)
main()
