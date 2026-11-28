"""Build and run a bounded portion of the local Combine-HT SST matrix."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time

from bootstrap_green import REVISIONS, SESSION, source_request
from transport import connect

HERE = Path(__file__).parent
REVISION = REVISIONS['a50893f']
FILES = ('fixture.py', 'prefill.cpp', 'sim_case.py', 'addressed_exports.py',
         'profile_case.py', 'clock_evidence.json')


def execute(client, filename, args, seconds, log):
    program = (HERE / filename).read_text(encoding='utf-8')
    program += '\nraise SystemExit(main(' + repr(args) + '))\n'
    stdin, stdout, stderr = client.exec_command(f'timeout --kill-after=25 {seconds} python3 -',
                                               timeout=seconds + 35)
    with ThreadPoolExecutor(max_workers=2) as readers:
        out = readers.submit(stdout.read, 65537)
        err = readers.submit(stderr.read, 16385)
        stdin.write(program)
        stdin.channel.shutdown_write()
        output = out.result()
        diagnostic = err.result()
    (log / (filename + '.stdout')).write_bytes(output)
    (log / (filename + '.stderr')).write_bytes(diagnostic)
    code = stdout.channel.recv_exit_status()
    assert len(output) <= 65536
    result = json.loads(output) if output else {}
    result.update(ssh_exit=code, ssh_stderr_bytes=len(diagnostic))
    return code, result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    host, auth, action = args['host'], args['auth'], args['action']
    assert host in ('26', '18') and auth in ('key', 'password')
    assert action in ('build-and-run', 'run', 'status', 'diagnose-kill', 'diagnose-log', 'build-entry-pipe', 'build-inline-post', 'build-fixed-events', 'recover-container', 'resize', 'finish')
    label = f'{host}-{action}-{time.time_ns()}'
    log = Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr' / SESSION / label
    log.mkdir(parents=True)
    report = {'host': host}
    with connect(host, auth) as client:
        args['host'] = 'host-94-' + host
        if action == 'build-and-run':
            source = source_request('a50893f', REVISION, log)
            source['host'] = args['host']
            code, build = execute(client, 'bootstrap_host.py', source, 510, log)
            report['build'] = {key: build[key] for key in ('revision', 'tree', 'image', 'environment_hashes',
                                                           'profile_build_seconds', 'profile_exit', 'profile',
                                                           'ssh_exit', 'ssh_stderr_bytes') if key in build}
            if code:
                print(json.dumps(report, separators=(',', ':')))
                return code
            args['action'] = 'run'
        seconds = 80
        if args['action'] in ('build-entry-pipe', 'build-inline-post', 'build-fixed-events'):
            args['build_files'] = {name: (HERE / name).read_text(encoding='utf-8')
                                   for name in ('build_profile.py', 'profile_capture.h')}
            seconds = 460
        if args['action'] == 'run':
            args['files'] = {name: (HERE / name).read_text(encoding='utf-8') for name in FILES}
            points = args['points']
            depth = (max(sum(point[0] == core for point in points) for core in (1, 2, 4, 8))
                     if args.get('parallel') else len(points))
            seconds += depth * (args['timeout'] + 1050) + 100
        code, result = execute(client, 'matrix_host.py', args, seconds, log)
        report.update(result)
    print(json.dumps(report, separators=(',', ':')))
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'error_class': type(error).__name__}))
        raise SystemExit(1)
