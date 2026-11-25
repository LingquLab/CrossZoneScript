"""Run bounded SSH commands using green-local authentication only."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

HOST = 'root@141.61.94.58'
AUTH_NOTE = 'D:/project/仿真配置/仿真系统环境信息.md'


def run(source, *, data=None, password=False, identity=None, timeout=60):
    argv = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10']
    if identity:
        argv += ['-i', str(identity), '-o', 'IdentitiesOnly=yes']
    if password:
        argv += ['-o', 'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1',
                 '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no']
    else:
        argv += ['-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no',
                 '-o', 'KbdInteractiveAuthentication=no']
    argv += [HOST, 'python3 -c ' + shlex.quote(source)]
    with tempfile.TemporaryDirectory(prefix='tilexr-auth-') as tmp:
        env = os.environ.copy()
        if password:
            py = Path(tmp) / 'auth.py'
            sh = Path(tmp) / 'askpass.sh'
            py.write_text(
                'from pathlib import Path\nimport re,sys\n'
                'raw=Path(' + repr(AUTH_NOTE) + ').read_bytes()\n'
                "try: text=raw.decode('utf-8-sig')\n"
                "except UnicodeDecodeError: text=raw.decode('gb18030')\n"
                "match=re.search(r'密码更新[：:]\\s*([^\\r\\n]+)',text)\n"
                'if not match: sys.exit(1)\n'
                'print(match.group(1).strip())\n', encoding='utf-8')
            sh.write_text('#!/bin/sh\nexec "' + Path(sys.executable).as_posix()
                          + '" "' + py.as_posix() + '"\n', encoding='utf-8', newline='\n')
            sh.chmod(0o700)
            env.update(SSH_ASKPASS=sh.as_posix(), SSH_ASKPASS_REQUIRE='force', DISPLAY='cross-zone:0')
        proc = subprocess.run(argv, input=json.dumps(data or {}).encode('ascii'),
                              capture_output=True, env=env, timeout=timeout)
    if proc.returncode:
        # Retain diagnostics locally; never export unfiltered stderr.
        Path('ssh-last-error.log').write_bytes(proc.stderr)
        raise RuntimeError('SSH exited ' + str(proc.returncode) + '; diagnostics retained locally')
    return json.loads(proc.stdout.decode('utf-8'))
