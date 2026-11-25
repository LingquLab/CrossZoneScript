"""Stop only the task's verified lock-owning downloader; preserve all partial files."""
import json
import os
from pathlib import Path
import signal
import time

status = Path('/home/glm16-09b20820/download-status.json')
d = json.loads(status.read_text())
assert d['target'] == '/mnt/share/GLM-5.2'
if d['state'] == 'completed':
    print(json.dumps({'state': 'already_completed'}))
else:
    assert d['state'] == 'running' and time.time() - d['updated_at'] < 120
    pid = d['pid']
    process = Path('/proc') / str(pid)
    assert (process / 'cmdline').read_bytes().split(b'\0')[:2] == [b'python3', b'-']
    lock = Path(d['target']) / '.glm16-download.lock'
    lock_inode = lock.stat().st_ino
    held = False
    for fd in (process / 'fd').iterdir():
        try:
            if os.readlink(fd) == str(lock) and fd.stat().st_ino == lock_inode:
                held = True
        except OSError:
            pass
    assert held, 'downloader_lock_identity_not_verified'
    os.kill(pid, signal.SIGTERM)
    for _ in range(40):
        if not process.exists() or (process / 'stat').read_text().split()[2] == 'Z':
            break
        time.sleep(0.25)
    else:
        raise RuntimeError('downloader_stop_unconfirmed')
    d.update(state='paused_for_resume', updated_at=time.time())
    tmp = status.with_suffix('.tmp')
    tmp.write_text(json.dumps(d))
    tmp.replace(status)
    print(json.dumps({'state': 'stopped_for_resume', 'partial_files_preserved': True,
                      'downloaded_bytes': d['downloaded_bytes'], 'files_done': d['files_done']}))
