"""Wait for verified weights, then check and benchmark the two owned NPU nodes."""
import concurrent.futures
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import urllib.request

HOSTS = ['141.61.94.58', '141.61.94.26']
PROJECT = '/home/glm16-09b20820'
ROOT = Path(PROJECT)
ROOT.mkdir(exist_ok=True)
run_id = 'run-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dt%H%M%S')
logs = ROOT / 'pipeline-runs' / run_id
logs.mkdir(parents=True)
lock = (ROOT / 'pipeline.lock').open('a+')
fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
deadline = time.monotonic() + ARGS.get('wait_seconds', 90000)
owned = {}
processes = {}
log_handles = []
state = {'run_id': run_id, 'phase': 'waiting_download', 'log_directory': str(logs)}
cleanup_records = []

def save(**values):
    state.update(values, updated_at=time.time(), containers=dict(owned))
    p = ROOT / 'pipeline-state.tmp'
    p.write_text(json.dumps(state, indent=2))
    p.replace(ROOT / 'pipeline-state.json')
    (logs / 'state.json').write_text(json.dumps(state, indent=2))

def ssh(host, argv, timeout=80):
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        '-o', 'ServerAliveInterval=30', 'root@' + host, *argv],
        capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        (logs / (host + '-last-ssh.stderr.log')).write_text(p.stderr)
        raise RuntimeError('remote_command_nonzero_' + host)
    return p.stdout

def helper(host, action, *args, timeout=110):
    return json.loads(ssh(host, ['python3', PROJECT + '/container_runtime.py', action, *args], timeout))

def model_readiness():
    source = r'''
import hashlib,json,os,pathlib,time
root=pathlib.Path('/home/glm16-09b20820');s=root/'download-status.json'
out={'complete':False}
if s.is_file():
 d=json.loads(s.read_text());out.update({k:d[k] for k in ['state','files_done','files_total','downloaded_bytes','total_bytes']})
 out['age_seconds']=round(time.time()-d['updated_at'],1)
 try:os.kill(d['pid'],0);out['downloader_alive']=True
 except ProcessLookupError:out['downloader_alive']=False
 marker=root/'download.complete.json'
 if marker.is_file():
  done=json.loads(marker.read_text());manifest=json.loads((root/'model-manifest.json').read_text())
  assert manifest['revision']=='4f470ee4c91fc9ea2318501a051e6015995e295d'
  assert done['files_done']==len(manifest['files']) and done['verified_bytes']==sum(x['size'] for x in manifest['files'])
  model=pathlib.Path('/mnt/share/GLM-5.2')
  assert hashlib.sha256((model/'config.json').read_bytes()).hexdigest()=='185f93ee6d12548e16a847e279dc0c3c90b1524c970b0866b42fb545747d859a'
  assert all((model/x['path']).is_file() and (model/x['path']).stat().st_size==x['size'] for x in manifest['files'])
  out['complete']=True
print(json.dumps(out))
'''
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        'root@' + HOSTS[0], 'python3', '-'], input=source, capture_output=True, text=True, timeout=35)
    assert p.returncode == 0, 'model_readiness_query_failed'
    return json.loads(p.stdout)

def wait_ready():
    while time.monotonic() < deadline:
        readiness = model_readiness()
        if not readiness['complete']:
            save(phase='waiting_download', download=readiness)
            assert readiness.get('state') != 'failed', 'downloader_failed'
            assert readiness.get('age_seconds', 0) < 600, 'downloader_status_stale'
            if readiness.get('state') != 'paused_for_resume':
                assert readiness.get('downloader_alive', True), 'downloader_not_alive'
            time.sleep(30)
            continue
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            gates = dict(zip(HOSTS, pool.map(lambda host: helper(host, 'gate'), HOSTS)))
        save(phase='waiting_idle', gates=gates, download=readiness)
        if all(x['idle'] for x in gates.values()):
            return
        time.sleep(30)
    raise TimeoutError('readiness_deadline')

def cleanup():
    errors = []
    for host, cid in list(owned.items()):
        try:
            result = helper(host, 'remove', '--id', cid, timeout=55)
            assert result.get('removed') or result.get('exists') is False
            cleanup_records.append({'host': host, 'id': cid, 'removed': True})
            owned.pop(host)
        except Exception as error:
            errors.append({'host': host, 'error_class': type(error).__name__})
    for host, process in list(processes.items()):
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)
        processes.pop(host)
    for stream in log_handles:
        stream.close()
    log_handles.clear()
    save(cleanup=cleanup_records, cleanup_errors=errors)
    if errors:
        raise RuntimeError('owned_container_cleanup_unconfirmed')

def start(kind):
    assert not owned and not processes
    save(phase=kind + '_creating')
    for host in HOSTS:
        result = helper(host, 'create', '--kind', kind, '--run-id', run_id, timeout=115)
        assert re.fullmatch('[0-9a-f]{64}', result['id'])
        owned[host] = result['id']
        save()
    for host in reversed(HOSTS):
        stream = (logs / (kind + '-' + host + '.log')).open('wb')
        log_handles.append(stream)
        processes[host] = subprocess.Popen(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=30', 'root@' + host, 'docker', 'start', '-a', owned[host]],
            stdout=stream, stderr=subprocess.STDOUT)
    save(phase=kind + '_running')

def smoke():
    start('smoke')
    try:
        end = time.monotonic() + 900
        while time.monotonic() < end:
            codes = {host: process.poll() for host, process in processes.items()}
            assert not any(x is not None and x != 0 for x in codes.values()), 'HCCL_smoke_process_failed'
            if all(x is not None for x in codes.values()):
                break
            time.sleep(2)
        else:
            raise TimeoutError('HCCL_smoke_deadline')
        ranks = {}
        for host in HOSTS:
            with (logs / ('smoke-' + host + '.log')).open(errors='replace') as stream:
                for line in stream:
                    marker = 'GLM_HCCL_SMOKE='
                    if marker in line:
                        record = json.loads(line.split(marker, 1)[1])
                        assert record['world'] == 16 and record['completed'] and record['expected'] == 136
                        ranks[record['rank']] = record
        assert set(ranks) == set(range(16)), 'HCCL_smoke_rank_records_missing'
        (logs / 'hccl-smoke.json').write_text(json.dumps(ranks, indent=2))
        save(smoke_ranks_verified=len(ranks))
    finally:
        cleanup()

def benchmark():
    start('model')
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        end = time.monotonic() + 3600
        while time.monotonic() < end:
            assert all(x.poll() is None for x in processes.values()), 'model_process_exited_before_ready'
            try:
                with opener.open('http://173.27.3.58:8016/health', timeout=3) as response:
                    if response.status == 200:
                        break
            except Exception:
                pass
            time.sleep(5)
        else:
            raise TimeoutError('model_startup_deadline')
        save(phase='measuring')
        output = PROJECT + '/results/' + run_id
        raw = ssh(HOSTS[0], ['python3', PROJECT + '/benchmark.py', '--endpoint',
                   'http://173.27.3.58:8016', '--output', output], timeout=950)
        metrics = json.loads(raw)
        assert metrics['measured_requests'] == 4 and metrics['warmup_requests'] == 1
        assert metrics['input_tokens'] == 8192 and metrics['output_tokens'] == 128
        (logs / 'performance.json').write_text(json.dumps(metrics, indent=2))
        save(performance=metrics, result_path=output)
        return metrics
    finally:
        cleanup()

def interrupted(signum, frame):
    raise InterruptedError('pipeline_interrupted')

signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGHUP, interrupted)
try:
    save()
    wait_ready()
    smoke()
    wait_ready()
    metrics = benchmark()
    save(phase='completed')
    print(json.dumps({'state': 'completed', 'run_id': run_id,
        'ttft_ms': metrics['mean_ttft_ms'], 'tpot_ms': metrics['mean_tpot_ms'],
        'e2e_s': metrics['mean_e2e_s'], 'results': state['result_path'], 'containers_removed': not owned}))
except Exception as error:
    try:
        cleanup()
    except Exception as cleanup_error:
        state['cleanup_error_class'] = type(cleanup_error).__name__
    save(phase='failed', error_class=type(error).__name__, error=str(error)[:300])
    print(json.dumps({'state': 'failed', 'run_id': run_id, 'error_class': type(error).__name__,
                       'logs': str(logs), 'containers_removed': not owned}))
    raise SystemExit(1)
