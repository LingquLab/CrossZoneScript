"""Run the requested Combine-HT points over the verified 26 SSH connection."""
import argparse
import json
import os
from pathlib import Path
import subprocess

import bootstrap_green

TOOLS = ('fixture.py', 'prefill.cpp', 'sim_case.py', 'return_case.py', 'addressed_exports.py',
         'profile_case.py', 'clock_evidence.json', 'inter_snapshot.py', 'joint_evidence.py',
         'build_profile.py', 'profile_capture.h')
POINTS = ('local-8x1', 'local-4x2', 'inter-16x8', 'inter-16x16', 'joint-16x8', 'joint-16x16')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', required=True, type=Path)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    here = Path(__file__).parent
    log = Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr' / bootstrap_green.SESSION
    log.mkdir(parents=True, exist_ok=True)
    phase = args['action']
    bs = args['bs']
    assert phase in ('build-and-run', 'resume', 'run', 'status') and bs in (4, 32, 128)
    if phase != 'status':
        points = args['points']
        assert points and points == [point for point in POINTS if point in points]
        assert type(args['timeout']) is int and 60 <= args['timeout'] <= 7200
    label = f"{phase}-{bs}-{'-'.join(args.get('points', []))}-r{args.get('retry', 0)}"
    result = {}
    if phase == 'build-and-run':
        build_args = log / 'build-args.json'
        build_args.write_text(json.dumps({'action': 'build'}))
        build = subprocess.run([os.sys.executable, str(here / 'bootstrap_green.py'),
                                '--args-file', str(build_args)], capture_output=True, timeout=1870)
        (log / 'build-wrapper.stdout').write_bytes(build.stdout)
        (log / 'build-wrapper.stderr').write_bytes(build.stderr)
        evidence = json.loads(build.stdout) if build.stdout.strip() else {}
        result['build'] = {key: evidence[key] for key in ('revision', 'image', 'build_exit', 'profile_exit',
                           'ssh_exit', 'stage', 'error_class') if key in evidence}
        for key in ('build', 'profile'):
            if isinstance(evidence.get(key), dict):
                result['build'][key + '_compiled'] = evidence[key].get('all_compiled')
        result['build']['exit_code'] = build.returncode
        if build.returncode:
            print(json.dumps(result, separators=(',', ':')))
            return build.returncode

    args['files'] = {name: (here / name).read_text(encoding='utf-8') for name in TOOLS}
    source = (here / 'matrix_host.py').read_text(encoding='utf-8')
    source += '''
import threading
finished = threading.Event()

def keepalive():
    while not finished.wait(12):
        print('sst-alive', flush=True)

heartbeat = threading.Thread(target=keepalive, daemon=True)
heartbeat.start()
try:
    code = main(''' + repr(args) + ''')
finally:
    finished.set()
    heartbeat.join()
raise SystemExit(code)
'''
    seconds = 90 if phase == 'status' else 80 + len(args['points']) * (args['timeout'] + {4:290, 32:460, 128:960}[bs])
    if phase == 'resume':
        seconds += 485
    key = Path(os.environ['USERPROFILE']) / '.ssh/id_ed25519'
    ssh = ['ssh', '-T', '-i', str(key), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
           '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=12', '-o', 'ServerAliveInterval=20',
           '-o', 'ServerAliveCountMax=3', 'root@141.61.94.26',
           f'timeout --kill-after=30 {seconds} python3 -']
    proc = subprocess.run(ssh, input=source.encode(), capture_output=True, timeout=seconds + 45)
    (log / f'{label}.stdout').write_bytes(proc.stdout)
    (log / f'{label}.stderr').write_bytes(proc.stderr)
    result.update(ssh_exit=proc.returncode, ssh_stderr_bytes=len(proc.stderr))
    lines = [line for line in proc.stdout.splitlines() if line and line != b'sst-alive']
    if lines:
        result.update(json.loads(lines[-1]))
    else:
        result['stage'] = 'matrix_ssh_or_remote_driver'
    print(json.dumps(result, separators=(',', ':')))
    return proc.returncode


if __name__ == '__main__':
    try:
        exit_code = main()
    except Exception as error:
        print(json.dumps({'stage': 'green_driver', 'error_class': type(error).__name__}))
        exit_code = 1
    raise SystemExit(exit_code)
