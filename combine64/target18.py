"""Run the pinned Combine-HT matrix on host-94-18 through the green bridge."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import time

from bootstrap_green import REVISIONS, SESSION, source_request
from matrix_green import POINTS, TOOLS

HOST = 'host-94-18'
ADDRESS = '141.61.94.18'
CANDIDATE = 'c030900'
DIRECTORY = Path(__file__).parent
READ_CREDENTIAL = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$path = Join-Path $env:LOCALAPPDATA 'CrossZone\credentials\141.61.94.26.clixml'
$credential = Import-Clixml -LiteralPath $path
if ($credential -isnot [System.Management.Automation.PSCredential] -or $credential.UserName -ne 'root' -or $credential.Password.Length -lt 1) { exit 3 }
[Console]::Write($credential.GetNetworkCredential().Password)
"""


def connect(client):
    profile = Path(os.environ['USERPROFILE'])
    client.load_host_keys(str(profile / '.ssh' / 'known_hosts'))
    assert client.get_host_keys().lookup(ADDRESS) is not None, 'trusted_host_key_missing'
    saved = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', READ_CREDENTIAL],
                           capture_output=True, timeout=14)
    assert saved.returncode == 0 and saved.stdout, 'saved_credential_unreadable'
    password = saved.stdout.decode('utf-8-sig')
    del saved
    try:
        client.connect(ADDRESS, port=22, username='root', password=password, allow_agent=False,
                       look_for_keys=False, timeout=10, auth_timeout=12, banner_timeout=10)
        client.get_transport().set_keepalive(15)
    finally:
        del password


def execute(client, entry, args, seconds, log):
    source = (DIRECTORY / entry).read_text(encoding='utf-8')
    source += '\nraise SystemExit(main(' + repr(args) + '))\n'
    stdin, stdout, stderr = client.exec_command(f'timeout --kill-after=25 {seconds} python3 -',
                                               timeout=seconds + 35)
    with ThreadPoolExecutor(max_workers=2) as pool:
        out = pool.submit(stdout.read, 65537)
        err = pool.submit(stderr.read, 16385)
        stdin.write(source)
        stdin.channel.shutdown_write()
        output = out.result()
        diagnostic = err.result()
    (log / f'{entry}.stdout').write_bytes(output)
    (log / f'{entry}.stderr').write_bytes(diagnostic)
    exit_code = stdout.channel.recv_exit_status()
    assert len(output) <= 65536, 'remote_output_too_large'
    if not output:
        return exit_code or 1, {'phase': entry, 'ssh_exit': exit_code, 'stderr_bytes': len(diagnostic)}
    report = json.loads(output)
    report['ssh_exit'] = exit_code
    return exit_code, report


def main():
    import paramiko

    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    action, bs = args['action'], args['bs']
    assert action in ('build-and-run', 'run', 'resume', 'status', 'sq-overlap', 'reuse-fault') and bs in (4, 32, 128)
    args['host'] = HOST
    if action not in ('status', 'sq-overlap', 'reuse-fault'):
        points = args['points']
        assert points and points == [point for point in POINTS if point in points]
        assert type(args['timeout']) is int and 60 <= args['timeout'] <= 7200
        assert type(args.get('retry', 0)) is int and 0 <= args.get('retry', 0) <= 10
    log = Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr' / SESSION / 'host18' / f'{action}-{bs}-{time.time_ns()}'
    log.mkdir(parents=True)
    report = {'host': HOST}
    with paramiko.SSHClient() as client:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        connect(client)
        if action == 'reuse-fault':
            code, evidence = execute(client, 'reuse_fault.py', args, 65, log)
            print(json.dumps(evidence, separators=(',', ':')))
            return code
        if action == 'sq-overlap':
            code, evidence = execute(client, 'sq_overlap.py', args, 220, log)
            print(json.dumps(evidence, separators=(',', ':')))
            return code
        if action == 'build-and-run':
            request = source_request(CANDIDATE, REVISIONS[CANDIDATE], log)
            request['host'] = HOST
            code, build = execute(client, 'bootstrap_host.py', request, 1800, log)
            build_report = {key: build[key] for key in ('revision', 'image', 'environment_hashes', 'build_exit',
                             'profile_exit', 'wall_seconds', 'profile_build_seconds', 'ssh_exit', 'stderr_bytes',
                             'phase') if key in build}
            for key in ('build', 'profile'):
                if key in build:
                    build_report[key + '_compiled'] = build[key].get('all_compiled')
            report['build'] = build_report
            if code:
                print(json.dumps(report, separators=(',', ':')))
                return code
            args['action'] = 'run'
        if action != 'status':
            args['files'] = {name: (DIRECTORY / name).read_text(encoding='utf-8') for name in TOOLS}
        seconds = 90
        if action != 'status':
            seconds += len(args['points']) * (args['timeout'] + {4:300, 32:490, 128:1000}[bs])
        if action == 'resume':
            seconds += 485
        if action != 'status' and any(point.startswith('joint') for point in args['points']):
            seconds += 485
        code, matrix = execute(client, 'matrix_host.py', args, seconds, log)
    report.update(matrix)
    print(json.dumps(report, separators=(',', ':')))
    return code


if __name__ == '__main__':
    try:
        result = main()
    except Exception as error:
        print(json.dumps({'host': HOST, 'error_class': type(error).__name__}, separators=(',', ':')))
        result = 1
    raise SystemExit(result)
