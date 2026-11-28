# 中文：协调原始双机EP32扇出V0/V1/V2，先短轮校验，再正反序采样；失败即停止后续依赖阶段。
# English: Coordinate original EP32 fanout smoke checks then forward/reverse sampling on two hosts; stop dependent stages on failure.
import concurrent.futures
import datetime
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
import uuid


def main():
    argument_file = Path(__file__).resolve().parents[2] / 'args.json'
    if argument_file.is_file():
        settings = json.loads(argument_file.read_text(encoding='utf-8-sig'))
    elif len(sys.argv) > 1 and sys.argv[1].lstrip().startswith('{'):
        settings = json.loads(sys.argv[1])
    else:
        raise ValueError('Missing experiment settings')
    keyroot = Path('C:/Users/l00826879/.ssh')
    keys = [p for p in keyroot.iterdir() if 'deploy' in p.name and p.is_file() and p.suffix.lower() not in ['.pub', '.old', '.bak'] and p.name not in ['config', 'known_hosts', 'authorized_keys'] and p.stat().st_size < 65536 and b'PRIVATE KEY' in p.read_bytes()]
    worker = Path(__file__).with_name('fanout_worker.py').read_text()
    hosts = ['90.90.93.83', '90.90.93.86']
    profiles=settings.get('profiles',[settings.get('profile','on')])
    assert profiles and len(set(profiles))==len(profiles) and all(p in ['on','off'] for p in profiles)
    sweeps=settings.get('sweeps',2)
    assert sweeps in [1,2]
    roots = ['/root/ubx-fanout-b212b5f4'] * 2
    logdir = Path.cwd() / ('ablation-' + uuid.uuid4().hex[:10])
    logdir.mkdir()

    def remote(node, config):
        config = dict(config, node=node, root=roots[node], revision=settings['revision'])
        command = 'python3 - ' + shlex.quote(json.dumps(config))
        args = ['ssh', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=NUL']
        for key in keys:
            args += ['-i', str(key)]
        try:
            p = subprocess.run(args + ['root@' + hosts[node], command], input=worker, capture_output=True, text=True, timeout=config.get('timeout', 30) + 220)
            (logdir / (config.get('run_id', 'check') + '-node%d.stderr' % node)).write_text(p.stderr)
            (logdir / (config.get('run_id','check')+'-node%d.stdout'%node)).write_text(p.stdout)
            r=json.loads(p.stdout)
            if p.returncode!=0:r['status']='FAIL';r['worker_exit']=p.returncode
        except Exception as e:
            r = {'status': 'ERROR', 'error_type': type(e).__name__}
        r['host'] = hosts[node]
        return r

    def both(config):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(lambda n: remote(n, config), range(2)))

    def emit(obj):
        obj['time_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with (logdir / 'events.jsonl').open('a') as f:
            f.write(json.dumps(obj) + '\n')
        exported=dict(obj)
        if obj.get('event') in ['smoke','experiment']:
            exported['results']=[]
            for node in obj['results']:
                ranks=node.get('ranks',[])
                selected={k:node[k] for k in ['host','status','timed_out','revision','fanout_pipeline','profiling','capture','network_accept_local'] if k in node}
                selected['rank_count']=len(ranks)
                selected['all_correct']=len(ranks)==16 and all(r.get('correct') and r.get('exit')==0 for r in ranks)
                for metric in ['dispatch_event_us','round_device_us','device_span_us']:
                    samples=[x for r in ranks for x in r.get(metric,[]) if isinstance(x,(int,float))]
                    slowest=[max(r[metric][i] for r in ranks) for i in range(len(ranks[0].get(metric,[])))] if selected['all_correct'] else []
                    if samples:
                        import statistics
                        selected[metric]={'count':len(samples),'mean':statistics.mean(samples),'median':statistics.median(samples),'min':min(samples),'max':max(samples),'slowest_rank_per_round':slowest}
                if not selected['all_correct']:
                    selected['failed_ranks']=[{k:r[k] for k in ['rank','exit','rounds'] if k in r} for r in ranks if not r.get('correct') or r.get('exit')!=0][:2]
                for metric in ['rdma_data_bytes','data_wqes','mem_records']:
                    values=[x for r in ranks for x in r.get(metric,[]) if isinstance(x,(int,float))]
                    if values:selected[metric]={'count':len(values),'min':min(values),'max':max(values),'mean':sum(values)/len(values)}
                exported['results'].append(selected)
        print(json.dumps(exported), flush=True)

    def run(bs, mt, smoke=False):
        config = {'action': 'run', 'variant': 'v%d-%s'%(settings['pipeline'],settings['profile']), 'pipeline': settings['pipeline'], 'diagnostic': False, 'profile': settings.get('profile', 'off'), 'gated': True, 'capture': uuid.uuid4().hex, 'run_id': 'fanout-v%d-%s-bs%d-mt%d-' % (settings['pipeline'],settings['profile'],bs,mt) + uuid.uuid4().hex[:12], 'bs': bs, 'mt': mt, 'rounds': 4 if smoke else 15, 'warmup': 2 if smoke else 5, 'timeout': 240 if smoke else 560, 'port': 24000 + int(uuid.uuid4().hex[:4], 16) % 10000}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(remote, node, config) for node in range(2)]
            gate_deadline = time.monotonic() + 75
            released = False
            while time.monotonic() < gate_deadline:
                if any(f.done() for f in futures):
                    break
                gates = both({'action': 'gate', 'run_id': config['run_id']})
                if all(g.get('ready') for g in gates):
                    released = True
                    both({'action': 'gate', 'run_id': config['run_id'], 'signal': 'go'})
                    break
                time.sleep(1)
            if not released:
                both({'action': 'gate', 'run_id': config['run_id'], 'signal': 'abort'})
            while not all(f.done() for f in futures):
                if any(f.done() and f.result().get('status') != 'PASS' for f in futures):
                    both({'action': 'gate', 'run_id': config['run_id'], 'signal': 'abort'})
                    break
                time.sleep(1)
            rows = [f.result() for f in futures]
        emit({'event': 'smoke' if smoke else 'experiment', 'bs': bs, 'mt': mt, 'run_id': config['run_id'], 'pipeline':settings['pipeline'],'profile':settings['profile'],'results': rows})
        if any(r.get('status') in ['BUSY', 'ABORTED'] for r in rows):
            return None
        return all(r.get('status') == 'PASS' for r in rows)

    for attempt in range(5):
        checks = both({'action': 'check'})
        if all(r.get('ready') for r in checks):break
        if attempt<4:time.sleep(30)
    emit({'event': 'resource_check', 'results': checks})
    if not all(r.get('ready') for r in checks):
        emit({'event': 'STOP', 'reason': 'resources_unavailable'})
        return 1
    for profile in profiles:
        settings['profile']=profile
        cases=[(True,v) for v in [0,1,2]]+[(False,v) for v in ([0,1,2]+([2,1,0] if sweeps==2 else []))]
        for smoke,pipeline in cases:
            settings['pipeline']=pipeline
            emit({'event':'case_begin','pipeline':pipeline,'profile':profile,'smoke':smoke})
            if not run(32,2,smoke):
                emit({'event':'STOP','passed':False,'pipeline':pipeline,'profile':profile})
                return 1
    emit({'event':'DONE','passed':True})
    return 0

raise SystemExit(main())
