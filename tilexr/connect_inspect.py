"""Use a green-local DPAPI credential; export only selected server facts."""
import base64
import json
from pathlib import Path
import shlex
import subprocess

import paramiko


def run(client, command, timeout=45):
    channel = client.get_transport().open_session(timeout=15)
    channel.settimeout(timeout)
    channel.exec_command(command)
    with channel.makefile('r') as stream:
        output = stream.read(262144)
    status = channel.recv_exit_status()
    channel.close()
    return status, output.decode('utf-8', errors='replace')


def main():
    credential_script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$path = Join-Path $env:LOCALAPPDATA 'CrossZone\credentials\141.61.94.26.clixml'
$credential = Import-Clixml -LiteralPath $path
@{user=$credential.UserName; password=$credential.GetNetworkCredential().Password} | ConvertTo-Json -Compress
'''
    read = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', credential_script],
                          capture_output=True, timeout=25)
    if read.returncode:
        raise RuntimeError('Saved credential could not be read by this Windows account')
    credential = json.loads(read.stdout.decode('utf-8-sig'))
    if credential['user'] != 'root':
        raise RuntimeError('Credential username does not match authorized root account')
    client = paramiko.SSHClient()
    client.load_host_keys('C:/Users/z00813871/.ssh/known_hosts')
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect('141.61.94.26', port=22, username='root', password=credential['password'],
                   allow_agent=False, look_for_keys=False, timeout=15, auth_timeout=20, banner_timeout=15)
    credential.clear()
    read = None
    result = {'password_login': 'success', 'ssh_key_configuration': 'not_attempted'}
    try:
        try:
            key = Path('C:/Users/z00813871/.ssh/id_ed25519')
            public_file = key.with_suffix('.pub')
            public_key = public_file.read_text().strip() if public_file.is_file() else ''
            if not public_key and key.is_file():
                derived = subprocess.run(['ssh-keygen', '-y', '-P', '', '-f', str(key)],
                                         capture_output=True, timeout=15)
                if derived.returncode == 0:
                    public_key = derived.stdout.decode('ascii').strip()
            fields = public_key.split()
            if len(fields) >= 2 and fields[0] == 'ssh-ed25519':
                base64.b64decode(fields[1], validate=True)
                public_key = ' '.join(fields[:2]) + ' crosszone-tilexr'
                script = '''set -eu
    umask 077
    test ! -L /root/.ssh
    test ! -L /root/.ssh/authorized_keys
    mkdir -p /root/.ssh
    chmod 700 /root/.ssh
    touch /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    key=$1
    if grep -qF -- "$key" /root/.ssh/authorized_keys; then
        printf 'already_present\\n'
    else
        printf '\\n%s\\n' "$key" >> /root/.ssh/authorized_keys
        printf 'added\\n'
    fi
    '''
                status, output = run(client, 'timeout 20 sh -c ' + shlex.quote(script) + ' sh ' + shlex.quote(public_key))
                result['ssh_key_configuration'] = output.strip() if status == 0 else 'could_not_install'
                if status == 0:
                    check = subprocess.run([
                        'ssh', '-i', str(key), '-p', '22', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                        '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=12',
                        'root@141.61.94.26', "printf 'CROSSZONE_KEY_OK'",
                    ], capture_output=True, timeout=25)
                    result['key_login_verified'] = check.returncode == 0 and check.stdout == b'CROSSZONE_KEY_OK'
            else:
                result['ssh_key_configuration'] = 'usable_public_key_not_found'
        except Exception as exc:
            result['ssh_key_configuration'] = 'could_not_configure_or_verify'
            result['ssh_key_error_type'] = type(exc).__name__
        remote = Path(__file__).with_name('inspect_server.py').read_text()
        status, output = run(client, 'timeout 100 python3 -c ' + shlex.quote(remote), timeout=110)
        if status != 0:
            raise RuntimeError('Server inspection failed; saved SSH authentication already succeeded')
        result['server'] = json.loads(output)
        print(json.dumps(result, ensure_ascii=True))
    finally:
        client.close()


if __name__ == '__main__':
    main()
