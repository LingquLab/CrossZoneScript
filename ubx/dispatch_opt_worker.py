import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


DEVICES = list(range(16))

def occupancy():
    def probe(i):
        try:
            p = subprocess.run(['npu-smi', 'info', '-t', 'proc-mem', '-i', str(i)], capture_output=True, text=True, timeout=12)
            return p.returncode == 0 and 'No process in device.' in p.stdout and 'Process id' not in p.stdout
        except Exception:
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(probe, DEVICES))


def main(c):
    root = Path('/root/ubx-dispatch-global-ab-d23ffa56') / c['variant']
    if c['action'] == 'check':
        cards=occupancy()
        return {'ready':all(cards),'unavailable':[i for i,x in enumerate(cards) if not x]}
    case=root/c['run_id']
    if c['action']=='gate':
        if c.get('signal') and case.is_dir(): (case/c['signal']).touch()
        return {'ready':(case/'ready').is_file()}
    if not all(occupancy()):return {'status':'BUSY','launched':False}
    case.mkdir(exist_ok=False);(case/'ready').touch()
    deadline=time.monotonic()+90
    while not (case/'go').is_file():
        if (case/'abort').is_file() or time.monotonic()>deadline:return {'status':'ABORTED','launched':False}
        time.sleep(.2)
    if not all(occupancy()):return {'status':'BUSY','launched':False}
    raw=subprocess.check_output(['bash','-c','source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1\nenv -0'])
    env=dict(x.decode().split('=',1) for x in raw.split(b'\0') if b'=' in x)
    env['LD_LIBRARY_PATH']=str(root/'build')+':'+str(root.parent/'runtime-install/lib')+':'+env.get('LD_LIBRARY_PATH','')
    env.update(TILEXR_RDMA_GID_INDEX=str(1 if c['node']==0 else 2),TILEXR_RDMA_PORT='1',TILEXR_RDMA_MTU='4096',TILEXR_RDMA_TIMEOUT_MS='30000')
    revision={'control':'771715cc8e2dd617a092246d8786f0576bb7fa81','global':'300d2fab3982d935fcba68b1d3b536e1445ec2e8'}[c['variant']]
    source=root/'source/dispatch/dispatch-release'
    measured=2 if c['rounds']==4 else 20
    cmd=['python3',str(source/'tests/hardware/run_dispatch_node.py'),'--host-index',str(c['node']),'--local-ranks','16','--devices']+[str(i) for i in DEVICES]+['--coordinator','90.90.93.81','--port',str(c['port']),'--build',str(root/'build'),'--output',str(case/'output'),'--bs','32','--nics']+['hrn5_'+str(i//2) for i in DEVICES]+['--tick-hz','1000000000','--revision',revision,'--measured',str(measured),'--profile-mode','full','--case-dir',str(root.parent/'inputs/ep32-full')]
    network=Path('/proc/sys/net/ipv4/conf/all/accept_local');original=network.read_text().strip()
    child=None;timed=False
    try:
        network.write_text('1\n')
        with (case/'controller.stdout.log').open('w') as out,(case/'controller.stderr.log').open('w') as err:
            child=subprocess.Popen(cmd,env=env,stdout=out,stderr=err,start_new_session=True)
            deadline=time.monotonic()+320
            while child.poll() is None:
                if (case/'abort').is_file() or time.monotonic()>deadline:
                    timed=True;break
                time.sleep(.5)
    finally:
        if child is not None and child.poll() is None:
            try:os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=5)
        network.write_text(original+'\n')
        assert network.read_text().strip()==original
    report=case/'output/host-result.json'
    result={'status':'FAIL','timed_out':timed,'exit':child.returncode,'variant':c['variant'],'profile_mode':'full','measured':measured,'warmup':10,'ranks':[],'network_restored':True}
    if report.is_file():
        data=json.loads(report.read_text());assert data['passed'] and len(data['ranks'])==16
        for rank in data['ranks']:
            rows=[json.loads(line) for line in (case/'output'/('rank'+str(rank['node']))/'rounds.jsonl').read_text().splitlines()]
            assert len(rows)==10+measured and all(row['verified'] for row in rows)
            result['ranks'].append({'rank':rank['rank'],'correct':True,'rounds':len(rows),'dispatch_event_us':[row['dispatch_kernel_us'] for row in rows if not row['warmup']]})
        if child.returncode==0 and not timed:result['status']='PASS'
    if result['status']!='PASS':
        result['controller_error_tail']=(case/'controller.stderr.log').read_text(errors='replace').splitlines()[-5:]
        result['worker_error_tails']=[{'rank':d.name,'tail':(d/'worker.log').read_text(errors='replace').splitlines()[-3:]} for d in sorted((case/'output').glob('rank*'))[:2] if (d/'worker.log').is_file()]
    (case/'result.json').write_text(json.dumps(result));return result

try:result=main(json.loads(sys.argv[1]))
except Exception as e:result={'status':'ERROR','error_type':type(e).__name__,'detail':str(e)[:300]}
print(json.dumps(result))
