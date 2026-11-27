"""Execute one bounded operational script on the verified simulator host."""
import json
import os
import re
from pathlib import Path

from check_sim_target import connect, load_target
from preflight_target import HOST, read_target_config

SESSION = 'be22947c-5e6b-4fb8-a268-af2f61eba704'


def run(code, args, label, seconds):
    import paramiko

    data, _ = read_target_config(args['fingerprint'])
    target = load_target(data, HOST)
    logs = Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr' / SESSION
    logs.mkdir(parents=True, exist_ok=True)

    def operation(client, deployment):
        request = {key: value for key, value in args.items() if key != 'fingerprint'}
        source = code + '\nraise SystemExit(main(' + repr({**request, **deployment}) + '))\n'
        command = f'timeout --kill-after=20 {seconds} python3 -'
        stdin, stdout, stderr = client.exec_command(command, timeout=seconds + 30)
        stdin.write(source)
        stdin.flush()
        stdin.channel.shutdown_write()
        output = stdout.read(65537)
        error = stderr.read(16385)
        exit_code = stdout.channel.recv_exit_status()
        (logs / f'{label}.stdout').write_bytes(output)
        (logs / f'{label}.stderr').write_bytes(error)
        try:
            result = json.loads(output) if len(output) <= 65536 else {}
        except (ValueError, UnicodeError):
            result = {}
        report = {'ssh_exit': exit_code, 'stderr_bytes': len(error), 'evidence': label, **result}
        if exit_code:
            detail = error.decode(errors='replace')
            report['exception'] = re.findall(r'(?m)^([A-Za-z]+Error):?', detail)[-3:]
            report['frames'] = re.findall(r'File "<stdin>", line (\d+), in (\w+)', detail)[-6:]
        print(json.dumps(report, separators=(',', ':')))
        return bool(result) if exit_code == 0 else bool(result or error)

    return connect(paramiko, target, operation)
