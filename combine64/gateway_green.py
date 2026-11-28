"""Run the pinned standalone GatewayReduce fixtures through the green SSH session."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time

from bootstrap_green import REVISIONS, SESSION, source_request
from transport import connect

HERE = Path(__file__).parent
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


CANDIDATE = '4d37ae6'
REVISION = REVISIONS[CANDIDATE]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    action = args['action']
    assert action in ('build', 'run', 'status', 'finish')
    log = Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr' / SESSION / f'{action}-{time.time_ns()}'
    log.mkdir(parents=True)
    with connect('18', 'password') as client:
        if action == 'build':
            source = source_request(CANDIDATE, REVISION, log)
            source['host'] = 'host-94-18'
            code, result = execute(client, 'bootstrap_host.py', source, 510, log)
        else:
            seconds = 60
            if action == 'run':
                args['files'] = {name: (HERE / name).read_text(encoding='utf-8') for name in (
                    'gateway_fixture.py', 'gateway_args.cpp', 'gateway_case.py', 'gateway_profile.py',
                    'addressed_exports.py', 'clock_evidence.json')}
                seconds = len(args['points']) * (args.get('timeout', 1800) + 480) + 80
            code, result = execute(client, 'gateway_host.py', args, seconds, log)
    print(json.dumps(result, separators=(',', ':')))
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'error_class': type(error).__name__}))
        raise SystemExit(1)
