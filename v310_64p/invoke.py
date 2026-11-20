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
        'build_single': 'build_softsync',
        'run_single': 'run',
        'status_single': 'status',
        'report_single': 'report',
    }
    assert operation in ('run', 'status', 'report', 'inspect', 'debug', 'probe', 'build_softsync', 'p2_profile',
                         'topology_check', *scripts)
    source = Path(__file__).with_name(scripts.get(operation, 'debug' if operation == 'probe' else operation) +
                                      '_remote.py').read_bytes()
    if operation in scripts:
        single = operation.endswith('_single')
        name = b'20261119_run13_single_round' if single else b'20261119_run09_softsync'
        source = source.replace(b'20261119_run07_corecount', name)
        if operation == 'build_single':
            source = b'single_round_probe = True\n' + source
        if operation == 'run_single':
            checksum = args['kernel_sha256']
            assert re.fullmatch(r'[0-9a-f]{64}', checksum)
            stage = Path(__file__).with_name('stage_single_round.py').read_bytes()
            source = ('single_round_binary_sha256 = ' + repr(checksum) + '\n').encode() + stage + b'\n' + source
            source = source.replace(b'1800s sst', b'3600s sst').replace(b'],1840)', b'],3640)')
        if operation == 'run_softsync':
            checksum = args['kernel_sha256']
            assert re.fullmatch(r'[0-9a-f]{64}', checksum)
            stage = Path(__file__).with_name('stage_softsync.py').read_bytes()
            source = ('softsync_binary_sha256 = ' + repr(checksum) + '\n').encode() + stage + b'\n' + source
    remote = 'docker exec -i simbox python3 -B -'
    if operation == 'p2_profile':
        source = Path(__file__).with_name('p2_profile_decode.py').read_bytes() + b'\n' + source
    if operation == 'topology_check':
        mode = args['mode']
        assert mode in ('smoke8', 'init64', 'sync64_clean')
        launcher = Path(__file__).with_name('topology_audited_launcher.py').read_bytes()
        source = (f'topology_mode = {mode!r}\nlauncher_source = {launcher!r}\n').encode() + source
    if operation in ('debug', 'probe'):
        controller = Path(__file__).with_name('run_remote.py').read_bytes()
        name = b'20261119_run08_host_gdb'
        if operation == 'probe':
            new_name = b'20261119_run12_sync_port_key'
            source = b'probe_mode = True\n' + source.replace(name, new_name)
            name = new_name
            controller = Path(__file__).with_name('stage_sync_probe.py').read_bytes() + b'\n' + controller
            controller = controller.replace(b'1800s sst', b'300s sst')
        controller = controller.replace(b'20261119_run07_corecount', name)
        source = ('run_script = ' + repr(controller) + '\n').encode() + source
        remote = 'python3 -B -'
    command = ['ssh', '-i', str(Path.home()/'.ssh/deploy_ed25519'),
               '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
               '-o', 'ConnectionAttempts=1', 'root@' + target,
               remote]
    start = time.monotonic()
    try:
        timeout = 4260 if operation == 'run_single' else {'run': 2400, 'status': 25, 'report': 650, 'inspect': 25,
                                          'debug': 2200, 'probe': 600, 'build_softsync': 210, 'p2_profile': 620,
                                          'topology_check': 490}
        process = subprocess.run(command, input=source, capture_output=True,
                                 timeout=timeout if isinstance(timeout, int) else timeout[scripts.get(operation, operation)])
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
