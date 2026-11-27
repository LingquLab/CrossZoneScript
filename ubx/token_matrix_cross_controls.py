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
    worker = Path(__file__).with_name('token_matrix_stage_worker.py').read_text()
    hosts = ['90.90.93.81', '90.90.93.83']
    assert settings.get('variant', 'control') in ['control', 'global']
    assert settings.get('profile', 'off') in ['off', 'on']
    roots = ['/root/ubx-token-merge-matrix-d23ffa56'] * 2
    logdir = Path.cwd() / ('ablation-' + uuid.uuid4().hex[:10])
    logdir.mkdir()

    def remote(node, config):
        config = dict(config, node=node, root=roots[node])
        command = 'python3 - ' + shlex.quote(json.dumps(config))
        args = ['ssh', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=NUL']
        for key in keys:
            args += ['-i', str(key)]
        try:
            p = subprocess.run(args + ['root@' + hosts[node], command], input=worker, capture_output=True, text=True, timeout=config.get('timeout', 30) + 90)
            (logdir / (config.get('run_id', 'check') + '-node%d.stderr' % node)).write_text(p.stderr)
            r = json.loads(p.stdout) if p.returncode == 0 else {'status': 'ERROR', 'reason': 'ssh_failed'}
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
        compact=dict(obj)
        if obj.get('event') in ['smoke','experiment']:
            compact['results']=[{k:r.get(k) for k in ['host','status','timed_out','bs','max_tokens','run_id','profiling','route_sha256']}|{'correct_ranks':sum(bool(x.get('correct')) for x in r.get('ranks',[]))} for r in obj['results']]
        print(json.dumps(compact), flush=True)

    def run(bs, mt, smoke=False):
        config = {'action': 'run', 'diagnostic': settings.get('diagnostic', False), 'profile': settings.get('profile', 'off'), 'gated': True, 'capture': uuid.uuid4().hex, 'run_id': 'ablation-bs%d-mt%d-' % (bs, mt) + uuid.uuid4().hex[:12], 'bs': bs, 'mt': mt, 'rounds': 4 if smoke else 15, 'warmup': 2 if smoke else 5, 'timeout': 90 if settings.get('diagnostic') else (240 if smoke else 560), 'port': 24000 + int(uuid.uuid4().hex[:4], 16) % 10000}
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
        emit({'event': 'smoke' if smoke else 'experiment', 'bs': bs, 'mt': mt, 'run_id': config['run_id'], 'results': rows})
        if any(r.get('status') in ['BUSY', 'ABORTED'] for r in rows):
            return None
        return all(r.get('status') == 'PASS' for r in rows)

    settings['diagnostic']=True
    phase=settings.get('phase','smoke')
    assert phase in ['smoke','formal']
    cases=[(64,1,'off'),(32,2,'on')]
    outcomes=[]
    for bs,mt,profile in cases:
        checks=both({'action':'check'})
        emit({'event':'resource_check','bs':bs,'mt':mt,'profile':profile,'results':checks})
        if not all(r.get('ready') for r in checks):
            emit({'event':'STOP','reason':'resources_unavailable'});return 1
        settings['profile']=profile
        outcome=run(bs,mt,phase=='smoke')
        outcomes.append(bool(outcome))
        emit({'event':'control_outcome','bs':bs,'mt':mt,'profile':profile,'passed':bool(outcome)})
    emit({'event':'DONE','passed':all(outcomes),'phase':phase,'cases':len(cases),'ep':32})
    return 0 if all(outcomes) else 1

raise SystemExit(main())
