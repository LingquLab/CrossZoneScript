"""Read the download receipt and verify the recorded process still exists."""
import json
import os
import fcntl
from pathlib import Path
import time

p = Path('/home/glm16-09b20820/download-status.json')
d = json.loads(p.read_text())
out = {k: d[k] for k in ['state', 'pid', 'target', 'files_done', 'files_total',
                         'verified_bytes', 'downloaded_bytes', 'total_bytes',
                         'elapsed_seconds']}
out['failure_count'] = len(d.get('failures', []))
out['failures'] = d.get('failures', [])[:3]
out['workers'] = d.get('workers', 4)
out['status_age_seconds'] = round(time.time() - d['updated_at'], 1)
try:
    os.kill(d['pid'], 0)
    out['process_alive'] = True
except ProcessLookupError:
    out['process_alive'] = False
lock = Path(d['target']) / '.glm16-download.lock'
if lock.is_file():
    with lock.open('r') as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            out['download_lock_held'] = False
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except BlockingIOError:
            out['download_lock_held'] = True
print(json.dumps(out))
