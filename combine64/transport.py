"""Use the green account's trusted SSH host keys and local credentials."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess

HOSTS = {"26": "141.61.94.26", "18": "141.61.94.18"}
READ_CREDENTIAL = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$path = Join-Path $env:LOCALAPPDATA 'CrossZone\credentials\141.61.94.26.clixml'
$credential = Import-Clixml -LiteralPath $path
if ($credential -isnot [System.Management.Automation.PSCredential] -or $credential.UserName -ne 'root' -or $credential.Password.Length -lt 1) { exit 3 }
[Console]::Write($credential.GetNetworkCredential().Password)
"""



@contextmanager
def connect(host, auth):
    import paramiko
    address = HOSTS[host]
    profile = Path(os.environ['USERPROFILE'])
    with paramiko.SSHClient() as client:
        client.load_host_keys(str(profile / '.ssh' / 'known_hosts'))
        assert client.get_host_keys().lookup(address) is not None, 'trusted_host_key_missing'
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        options = dict(allow_agent=False, look_for_keys=False, timeout=12, auth_timeout=12, banner_timeout=12)
        if auth == 'key':
            options['key_filename'] = str(profile / '.ssh' / 'id_ed25519')
        else:
            assert auth == 'password'
            saved = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', READ_CREDENTIAL],
                                   capture_output=True, timeout=14)
            assert saved.returncode == 0 and saved.stdout, 'saved_credential_unreadable'
            options['password'] = saved.stdout.decode('utf-8-sig')
        try:
            client.connect(address, port=22, username='root', **options)
        finally:
            options.pop('password', None)
        client.get_transport().set_keepalive(15)
        yield client


def remote(client, command, *, timeout):
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read(65537)
    diagnostic = stderr.read(16385)
    assert len(output) <= 65536, 'remote_output_too_large'
    return stdout.channel.recv_exit_status(), output, diagnostic
