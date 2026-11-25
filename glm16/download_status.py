"""Read the download receipt and verify the recorded process still exists."""
import json
import os
from pathlib import Path
import time

p = Path('/home/glm16-09b20820/download-status.json')
d = json.loads(p.read_text())
out = {k: d[k] for k in ['state', 'pid', 'target', 'files_done', 'files_total',
                         'verified_bytes', 'downloaded_bytes', 'total_bytes',
                         'elapsed_seconds', 'failures']}
out['workers'] = d.get('workers', 4)
out['status_age_seconds'] = round(time.time() - d['updated_at'], 1)
try:
    os.kill(d['pid'], 0)
    out['process_alive'] = True
except ProcessLookupError:
    out['process_alive'] = False
print(json.dumps(out))
