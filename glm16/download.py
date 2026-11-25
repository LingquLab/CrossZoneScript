"""Download and hash the pinned GLM snapshot, with bounded retries and resume."""
import concurrent.futures
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request

ROOT = Path(ARGS['target'])
assert str(ROOT) == '/mnt/share/GLM-5.2'
STATE = Path('/home/glm16-09b20820')
manifest = json.loads((STATE / 'model-manifest.json').read_text())
assert manifest['model'] == 'Eco-Tech/GLM-5.2-w4a4c8-mxfp4'
assert manifest['revision'] == '4f470ee4c91fc9ea2318501a051e6015995e295d'
FILES = manifest['files']
assert sum(x['path'].endswith('.safetensors') for x in FILES) == 100
assert all(re.fullmatch('[0-9a-f]{64}', x.get('sha256') or '') for x in FILES)
ROOT.mkdir(exist_ok=True)
lock_file = (ROOT / '.glm16-download.lock').open('a+')
fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
PARTS = ROOT / '.glm16-partials'
PARTS.mkdir(exist_ok=True)
status_path = STATE / 'download-status.json'
total = sum(x['size'] for x in FILES)
space = os.statvfs(ROOT)
assert space.f_bavail * space.f_frsize > total + 20 * 1024**3, 'insufficient_free_space'
budget = ARGS.get('download_seconds', 86400)
workers = ARGS.get('workers', 16)
assert isinstance(budget, int) and 3600 <= budget <= 172800
assert isinstance(workers, int) and 1 <= workers <= 32
deadline = time.monotonic() + budget
guard = threading.Lock()
done = set()
active = {}
failures = []
stop = threading.Event()
started = time.time()

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def save_status(state):
    with guard:
        completed = sum(x['size'] for x in FILES if x['path'] in done)
        partial = sum(active.values())
        value = {'state': state, 'pid': os.getpid(), 'target': str(ROOT),
                 'files_done': len(done), 'files_total': len(FILES),
                 'verified_bytes': completed, 'downloaded_bytes': completed + partial,
                 'total_bytes': total, 'elapsed_seconds': round(time.time()-started, 1),
                 'updated_at': time.time(), 'workers': workers, 'failures': list(failures)}
    tmp = status_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value))
    tmp.replace(status_path)
    return value

def transfer(item):
    name, size, expected = item['path'], item['size'], item['sha256']
    relative = Path(name)
    assert not relative.is_absolute() and '..' not in relative.parts
    dest, part = ROOT / relative, PARTS / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    part.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        assert dest.is_file() and dest.stat().st_size == size and digest(dest) == expected, 'existing_file_mismatch'
        with guard:
            done.add(name)
        return
    url = ('https://modelscope.cn/models/' + manifest['model'] + '/resolve/'
           + manifest['revision'] + '/' + urllib.parse.quote(name, safe='/'))
    for attempt in range(3):
        if stop.is_set() or time.monotonic() >= deadline:
            raise TimeoutError('download_deadline')
        try:
            offset = part.stat().st_size if part.exists() else 0
            assert offset <= size, 'partial_file_oversized'
            with guard:
                active[name] = offset
            if offset < size:
                headers = {'User-Agent': 'GLM16-validation/1'}
                if offset:
                    headers['Range'] = f'bytes={offset}-'
                opener = urllib.request.build_opener(urllib.request.ProxyHandler(PROXIES),
                    urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
                request = urllib.request.Request(url, headers=headers)
                with opener.open(request, timeout=90) as response:
                    if offset:
                        assert response.status == 206 and response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'), 'resume_not_honored'
                    else:
                        assert response.status in (200, 206)
                    with part.open('ab') as stream:
                        while offset < size:
                            if stop.is_set() or time.monotonic() >= deadline:
                                raise TimeoutError('download_deadline')
                            chunk = response.read(min(8 * 1024 * 1024, size-offset))
                            if not chunk:
                                raise EOFError('incomplete_download')
                            stream.write(chunk)
                            offset += len(chunk)
                            with guard:
                                active[name] = offset
            assert part.stat().st_size == size and digest(part) == expected, 'sha256_mismatch'
            assert not dest.exists(), 'destination_appeared'
            os.replace(part, dest)
            with guard:
                active.pop(name, None)
                done.add(name)
            return
        except Exception:
            if attempt == 2 or stop.is_set():
                raise
            time.sleep(2 * (attempt + 1))

save_status('running')
with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
    pending = {pool.submit(transfer, x): x for x in sorted(FILES, key=lambda x: x['size'])}
    while pending:
        finished, _ = concurrent.futures.wait(pending, timeout=5,
                                            return_when=concurrent.futures.FIRST_COMPLETED)
        for future in finished:
            item = pending.pop(future)
            try:
                future.result()
            except Exception as error:
                with guard:
                    failures.append({'file': item['path'], 'error_class': type(error).__name__,
                                     'http_status': getattr(error, 'code', None)})
                stop.set()
        save_status('failed' if failures else 'running')
final = save_status('failed' if failures else 'completed')
if not failures:
    assert len(done) == len(FILES)
    (STATE / 'download.complete.json').write_text(json.dumps(final))
print(json.dumps({'state': final['state'], 'target': str(ROOT), 'files': len(done),
                  'bytes': final['verified_bytes'], 'elapsed_seconds': final['elapsed_seconds']}))
if failures:
    raise SystemExit(1)
