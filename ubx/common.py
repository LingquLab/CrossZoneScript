"""中文：当前会话的密码 SSH、固定版本和本地证据；供构建和测试脚本调用。
English: Session-local password SSH, immutable provenance, and retained evidence.
"""
import json
import os
from pathlib import Path
import re
import subprocess

REVISION = '75076b89d4750a7fd5db2e5b85d124274d22b62f'
HOSTS = ['90.90.93.84', '90.90.93.184']
REMOTE_ROOT = '/root/ubx-combine-75076b89-e5db7753'
ROOT = Path.cwd()


def credentials():
    result = {}
    current = None
    manual = Path(r'D:\project\UBX攻关\环境信息\实机手册.md')
    for line in manual.read_text(encoding='utf-8-sig').splitlines():
        found = [h for h in HOSTS if h in line]
        if len(found) == 1:
            current = found[0]
        match = re.search(r'(?:密码|password|passwd)\s*[:：=]?\s*(\S+)', line, re.I)
        if match and current:
            result[current] = match[1].strip('`\"\' ')
    assert set(result) == set(HOSTS)
    return result


def ssh(host, command, payload=None, timeout=60, label='ssh'):
    helper = ROOT / 'ep32-askpass.sh'
    assert helper.is_file()
    env = os.environ.copy()
    env.update(SSH_ASKPASS=str(helper), SSH_ASKPASS_REQUIRE='force', DISPLAY=':0',
               EP32_SSH_PASSWORD=credentials()[host])
    argv = [r'C:\Program Files\Git\usr\bin\ssh.exe', '-T', '-o', 'ConnectTimeout=8',
            '-o', 'ConnectionAttempts=1', '-o', 'PreferredAuthentications=password',
            '-o', 'PubkeyAuthentication=no', '-o', 'NumberOfPasswordPrompts=1',
            '-o', 'StrictHostKeyChecking=accept-new', 'root@' + host, *command]
    result = subprocess.run(argv, input=payload, capture_output=True, env=env, timeout=timeout)
    (ROOT / (label + '-' + host + '.stdout')).write_bytes(result.stdout)
    (ROOT / (label + '-' + host + '.stderr')).write_bytes(result.stderr)
    return result


def remote_python(host, script, timeout=60, label='remote'):
    payload = ("set -eu\npython3 -B - <<'EP32_SCRIPT'\n" + script + '\nEP32_SCRIPT\n').encode()
    return ssh(host, ['bash', '-s'], payload, timeout, label)


def json_rows(raw):
    rows = []
    for line in raw.decode('utf-8', errors='replace').splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows
