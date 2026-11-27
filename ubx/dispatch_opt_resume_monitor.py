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
    worker = Path(__file__).with_name('dispatch_opt_worker.py').read_text()
    hosts = ['90.90.93.81', '90.90.93.83']
    assert settings.get('variant', 'control') in ['control', 'global']
    assert settings.get('profile', 'off') in ['off', 'on']
    roots = ['/root/ubx-dispatch-global-ab-d23ffa56/' + settings.get('variant', 'control')] * 2
    logdir = Path.cwd() / ('ablation-' + uuid.uuid4().hex[:10])
    logdir.mkdir()

    def remote(node, config):
        config = dict(config, node=node, root=roots[node], variant=settings.get('variant','global'))
        command = 'python3 - ' + shlex.quote(json.dumps(config))
        args = ['ssh', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=NUL']
        for key in keys:
            args += ['-i', str(key)]
        try:
            p = subprocess.run(args + ['root@' + hosts[node], command], input=worker, capture_output=True, text=True, timeout=(420 if config.get('action')=='run' else 90))
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
        compact = dict(obj)
        if obj.get('event') in ['smoke', 'experiment']:
            compact['results'] = [{'host':x.get('host'), 'status':x.get('status'), 'timed_out':x.get('timed_out'), 'completed_ranks':sum(v.get('rounds',0)>0 for v in x.get('ranks',[])), 'correct_ranks':sum(bool(v.get('correct')) for v in x.get('ranks',[]))} for x in obj.get('results',[])]
        print(json.dumps(compact), flush=True)

    def run(bs, mt, smoke=False):
        config = {'action': 'run', 'diagnostic': settings.get('diagnostic', False), 'profile': settings.get('profile', 'off'), 'gated': True, 'capture': uuid.uuid4().hex, 'run_id': 'ablation-bs%d-mt%d-' % (bs, mt) + uuid.uuid4().hex[:12], 'bs': bs, 'mt': mt, 'rounds': 4 if smoke else 25, 'warmup': 2 if smoke else 5, 'timeout': 90 if settings.get('diagnostic') else (240 if smoke else 560), 'port': 24000 + int(uuid.uuid4().hex[:4], 16) % 10000}
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

    statefile = Path.cwd() / 'monitor-state.json'
    phases = [('global', False), ('control', False)]
    phase = 0
    deadline = time.monotonic() + 21600
    while time.monotonic() < deadline:
        variant, smoke = phases[phase]
        roots[:] = ['/root/ubx-dispatch-global-ab-d23ffa56/' + variant] * 2
        settings.update(variant=variant, profile='on', diagnostic=False)
        checks = both({'action': 'check'})
        emit({'event': 'resource_check', 'phase': phase, 'variant':variant, 'results': checks})
        statefile.write_text(json.dumps({'phase':phase,'variant':variant,'smoke':smoke,'status':'WAITING','checked_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}))
        if not all(r.get('ready') for r in checks):
            time.sleep(min(300, max(0, deadline-time.monotonic())))
            continue
        statefile.write_text(json.dumps({'phase':phase,'variant':variant,'smoke':smoke,'status':'RUNNING'}))
        outcome = run(32, 2, smoke)
        if outcome is None:
            time.sleep(min(300, max(0, deadline-time.monotonic())))
            continue
        if not outcome:
            emit({'event':'STOP','reason':'validation_failed','phase':phase,'variant':variant})
            statefile.write_text(json.dumps({'phase':phase,'variant':variant,'status':'VALIDATION_FAILED'}))
            return 1
        phase += 1
        statefile.write_text(json.dumps({'completed_phases':phase,'status':'NEXT_PHASE' if phase<len(phases) else 'DONE'}))
        if phase == len(phases):
            emit({'event':'DONE','passed':True,'formal_rounds':30,'warmup_rounds':10,'profiling':True})
            return 0
    emit({'event':'MONITOR_WINDOW_ENDED','reason':'six_hour_window_elapsed','phase':phase})
    return 0

raise SystemExit(main())
