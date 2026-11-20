"""Install the interactive credential saver and test the authorized existing key."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    if os.name != 'nt':
        raise RuntimeError('Expected the green Windows worker')
    root = Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr'
    root.mkdir(parents=True, exist_ok=True)
    saver = root / 'save-server-password.ps1'
    shutil.copyfile(Path(__file__).with_name(saver.name), saver)
    key = Path('C:/Users/z00813871/.ssh/id_ed25519')
    result = {
        'password_saver': str(saver),
        'credential_saved': (root.parent / 'credentials' / '141.61.94.26.clixml').is_file(),
        'key_present': key.is_file(),
        'paramiko_available': importlib.util.find_spec('paramiko') is not None,
        'ssh_available': shutil.which('ssh') is not None,
        'key_auth': 'not_tested',
    }
    if result['key_present'] and result['ssh_available']:
        command = [
            'ssh', '-i', str(key), '-p', '22', '-o', 'BatchMode=yes',
            '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=accept-new',
            '-o', 'ConnectTimeout=12', '-o', 'ConnectionAttempts=1',
            'root@141.61.94.26', "printf 'CROSSZONE_SSH_OK\\n'; uname -m; command -v docker",
        ]
        try:
            process = subprocess.run(command, capture_output=True, timeout=25)
            lines = process.stdout.decode('utf-8', errors='replace').splitlines()
            result['key_auth'] = 'success' if process.returncode == 0 and 'CROSSZONE_SSH_OK' in lines else 'unavailable'
            result['ssh_exit_code'] = process.returncode
            if result['key_auth'] == 'success':
                index = lines.index('CROSSZONE_SSH_OK')
                result['architecture'] = lines[index + 1] if index + 1 < len(lines) else 'unknown'
                result['docker_present'] = any(line.endswith('/docker') for line in lines[index + 2:])
            else:
                errors = process.stderr.decode('utf-8', errors='replace').lower()
                result['ssh_failure'] = next((name for pattern, name in [
                    ('permission denied', 'authentication_denied'),
                    ('timed out', 'connection_timeout'),
                    ('host key verification failed', 'host_key_verification_failed'),
                    ('connection refused', 'connection_refused'),
                    ('bad permissions', 'key_permissions'),
                ] if pattern in errors), 'ssh_failed')
        except subprocess.TimeoutExpired:
            result['key_auth'] = 'connection_timeout'
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
