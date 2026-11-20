"""Update and validate the saver without asking for or accessing a password."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    if os.name != 'nt':
        raise RuntimeError('Expected the green Windows worker')
    directory = Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr'
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / 'save-server-password.ps1'
    shutil.copyfile(Path(__file__).with_name(destination.name), destination)
    process = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
         '-File', str(destination), '-PrepareOnly'],
        capture_output=True, timeout=30,
    )
    ready = process.returncode == 0 and b'credential_directory_ready' in process.stdout
    print(json.dumps({'password_saver': str(destination), 'acl_verified': ready,
                      'password_prompted': False, 'powershell_exit_code': process.returncode}))
    if not ready:
        # A bounded diagnostic, with no credential read or prompt in this mode.
        sys.stderr.buffer.write(process.stderr[:4000])
        raise SystemExit(1)


if __name__ == '__main__':
    main()
