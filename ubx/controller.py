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
    keys = [p for p in keyroot.iterdir() if p.is_file() and p.suffix.lower() not in ['.pub', '.old', '.bak'] and p.name not in ['config', 'known_hosts', 'authorized_keys'] and p.stat().st_size < 65536 and b'PRIVATE KEY' in p.read_bytes()]
    worker = Path(__file__).with_name('worker.py').read_text()
    hosts = ['90.90.93.84', '90.90.93.188']
    roots = ['/root/ubx-shared-off25-cad5b90-ep32-e384-c56', '/root/ubx-ablation-d23ffa56-cad5b90']
    logdir = Path.cwd() / ('ablation-' + uuid.uuid4().hex[:10])
    logdir.mkdir()

    def remote(node, config):
        config = dict(config, node=node, root=roots[node])
        command = 'python3 -c ' + shlex.quote(worker) + ' ' + shlex.quote(json.dumps(config))
        args = ['ssh', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=NUL']
        for key in keys:
            args += ['-i', str(key)]
        try:
            p = subprocess.run(args + ['root@' + hosts[node], command], capture_output=True, text=True, timeout=config.get('timeout', 30) + 90)
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
        print(json.dumps(obj), flush=True)

    def run(bs, mt, smoke=False):
        config = {'action': 'run', 'run_id': 'ablation-bs%d-mt%d-' % (bs, mt) + uuid.uuid4().hex[:12], 'bs': bs, 'mt': mt, 'rounds': 3 if smoke else 25, 'warmup': 1 if smoke else 5, 'timeout': 240 if smoke else 560, 'port': 24000 + int(uuid.uuid4().hex[:4], 16) % 10000}
        rows = both(config)
        emit({'event': 'smoke' if smoke else 'experiment', 'bs': bs, 'mt': mt, 'run_id': config['run_id'], 'results': rows})
        return all(r.get('status') == 'PASS' for r in rows)

    deadline = time.monotonic() + settings.get('hours', 8) * 3600
    smoke = settings.get('mode', 'smoke') == 'smoke'
    validated = False
    queue = [(64, 2)] if smoke else [(bs, mt) for bs in [64, 128] for mt in [1, 2, 3, 4]]
    while queue and time.monotonic() < deadline:
        checks = both({'action': 'check'})
        emit({'event': 'resource_check', 'results': checks})
        if not all(r.get('ready') for r in checks):
            if smoke:
                emit({'event': 'STOP', 'reason': 'resources_unavailable'})
                return 1
            wake = min(time.monotonic() + 600, deadline)
            while time.monotonic() < wake:
                time.sleep(min(30, max(0, wake - time.monotonic())))
            continue
        if not smoke and not validated:
            if not run(64, 2, True):
                emit({'event': 'STOP', 'reason': 'smoke_validation_failed', 'remaining': queue})
                return 1
            validated = True
            continue
        bs, mt = queue[0]
        if not run(bs, mt, smoke):
            emit({'event': 'STOP', 'reason': 'validation_failed_or_incomplete', 'remaining': queue})
            return 1
        queue.pop(0)
    emit({'event': 'DONE' if not queue else 'WINDOW_EXPIRED', 'remaining': queue})
    return 0 if not queue else 1


raise SystemExit(main())
