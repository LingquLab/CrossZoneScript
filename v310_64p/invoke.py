import argparse
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', required=True, type=Path)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    target = str(ipaddress.IPv4Address(args['target']))
    operation = args['operation']
    scripts = {
        'run_softsync': 'run',
        'status_softsync': 'status',
        'report_softsync': 'report',
    }
    assert operation in ('run', 'status', 'report', 'inspect', 'debug', 'build_softsync', *scripts)
    source = Path(__file__).with_name(scripts.get(operation, operation) + '_remote.py').read_bytes()
    if operation in scripts:
        source = source.replace(b'20261119_run07_corecount', b'20261119_run09_softsync')
        if operation == 'run_softsync':
            checksum = args['kernel_sha256']
            assert re.fullmatch(r'[0-9a-f]{64}', checksum)
            stage = Path(__file__).with_name('stage_softsync.py').read_bytes()
            source = ('softsync_binary_sha256 = ' + repr(checksum) + '\n').encode() + stage + b'\n' + source
    remote = 'docker exec -i simbox python3 -B -'
    if operation == 'debug':
        controller = Path(__file__).with_name('run_remote.py').read_bytes()
        controller = controller.replace(b'20261119_run07_corecount', b'20261119_run08_host_gdb')
        source = ('run_script = ' + repr(controller) + '\n').encode() + source
        remote = 'python3 -B -'
    command = ['ssh', '-i', str(Path.home()/'.ssh/deploy_ed25519'),
               '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
               '-o', 'ConnectionAttempts=1', 'root@' + target,
               remote]
    start = time.monotonic()
    try:
        process = subprocess.run(command, input=source, capture_output=True,
                                 timeout={'run': 2400, 'status': 25, 'report': 650, 'inspect': 25,
                                          'debug': 2200, 'build_softsync': 210}
                                 [scripts.get(operation, operation)])
        try:
            result = json.loads(process.stdout) if process.returncode == 0 else {
                'status': 'SSH_OR_REMOTE_FAILURE', 'sst_start_verified': False,
            }
        except ValueError:
            result = {'status': 'REMOTE_OUTPUT_NOT_JSON', 'sst_start_verified': False}
        result['ssh_exit_code'] = process.returncode
    except (subprocess.TimeoutExpired, OSError) as error:
        result = {'status': type(error).__name__, 'sst_start_verified': False}
    result['bridge_ssh_wall_seconds'] = round(time.monotonic() - start, 3)
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
