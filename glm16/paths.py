"""Check the authorized download target and its mount without replacing files."""
import json
import os
from pathlib import Path
import subprocess

out = {'paths': []}
for p in [Path('/mnt/weight'), Path('/mnt/weight/GLM-5.2'), Path('/home/glm16-09b20820')]:
    rec = {'path': str(p)}
    try:
        st = p.stat()
        fs = os.statvfs(p)
        rec.update(exists=True, mode=oct(st.st_mode & 0o7777), uid=st.st_uid, gid=st.st_gid,
                   readonly=bool(fs.f_flag & os.ST_RDONLY), free_bytes=fs.f_bavail * fs.f_frsize)
    except OSError as e:
        rec.update(error_class=type(e).__name__, errno=e.errno, strerror=e.strerror)
    out['paths'].append(rec)
try:
    Path('/mnt/weight/GLM-5.2').mkdir(exist_ok=True)
    out['target_directory_ready'] = True
except OSError as e:
    out['mkdir'] = {'error_class': type(e).__name__, 'errno': e.errno, 'strerror': e.strerror}
p = subprocess.run(['findmnt', '-rn', '-T', '/mnt/weight', '-o', 'TARGET,SOURCE,OPTIONS'],
                   capture_output=True, text=True, timeout=10)
out['mount'] = p.stdout[:1800]
out['manifest_exists'] = Path('/home/glm16-09b20820/model-manifest.json').is_file()
print(json.dumps(out))
