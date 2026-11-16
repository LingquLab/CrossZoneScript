"""One bounded SSH evidence check; preserve Bridge-owned files."""
from pathlib import Path
import hashlib
import json
import subprocess

work = Path(__file__).resolve().parent
remote = (work / 'preflight_remote.py').read_bytes()
opts = ['-i', str(Path.home() / '.ssh/id_ed25519'), '-o', 'IdentitiesOnly=yes',
        '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15',
        '-o', 'ConnectionAttempts=1']
try:
    p = subprocess.run(['ssh', *opts, 'root@141.61.73.107', '/usr/bin/python3 -'],
                       input=remote, capture_output=True, timeout=110)
except subprocess.TimeoutExpired as error:
    (work / 'p2-preflight-ssh.stdout.log').write_bytes(error.stdout or b'')
    (work / 'p2-preflight-ssh.stderr.log').write_bytes(error.stderr or b'')
    (work / 'p2-preflight-ssh.receipt.json').write_text(json.dumps({'timed_out': True}))
    raise RuntimeError('Preflight timeout; read saved evidence; no replay') from None
(work / 'p2-preflight-ssh.stdout.log').write_bytes(p.stdout)
(work / 'p2-preflight-ssh.stderr.log').write_bytes(p.stderr)
(work / 'p2-preflight-ssh.receipt.json').write_text(json.dumps({'exit_code': p.returncode,
    'remote_source_sha256': hashlib.sha256(remote).hexdigest()}))
if not p.stdout.strip().startswith(b'{'):
    print(json.dumps({'status': 'FAIL_OR_UNVERIFIED', 'ssh_exit_code': p.returncode,
                      'npu_launch': False, 'detail': 'Inspect saved SSH evidence without replay'}))
    raise SystemExit(1)
r = json.loads(p.stdout)
assert r['schema'] == 'tilelang_f9_p2_107_preflight.v1' and r['devices'] == [0, 1]
assert r['npu_launch'] is False and r['acl_initialization'] is False
text = json.dumps(r, separators=(',', ':'), ensure_ascii=True)
assert len(text.encode()) < 9000
print(text)
raise SystemExit(0 if p.returncode == 0 and r['status'] == 'PASS' else 1)
