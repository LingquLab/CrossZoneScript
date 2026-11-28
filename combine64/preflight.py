"""Check the requested host 18 and its reusable SST image and template."""
import argparse
import json

from transport import connect, remote

IMAGE = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
TEMPLATE = '/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549'


def probe(host, auth):
    row = dict(host=host, auth=auth)
    try:
        with connect(host, auth) as client:
            command = (
                'hostname -s && uname -m && '
                'docker image inspect --format "{{.Id}} {{.Os}}/{{.Architecture}}" ' + IMAGE +
                ' && test -r ' + TEMPLATE + '/case-manifest.json' +
                ' && df -BG --output=avail /mnt/share/tilexr-sim-jobs | tail -1'
            )
            code, output, error = remote(client, command, timeout=20)
            rows = output.decode('ascii', errors='replace').splitlines()
            row.update(ssh_exit=code, stderr_bytes=len(error))
            row['ready'] = code == 0 and len(rows) == 4 and rows[:3] == [
                'host-94-' + host, 'aarch64', IMAGE + ' linux/arm64']
            if row['ready']:
                row['available_gib'] = int(rows[3].strip().rstrip('G'))
    except Exception as error:
        row.update(ready=False, error_class=type(error).__name__)
    return row


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', required=True)
    parser.parse_args()
    try:
        result = probe('18', 'password')
    except Exception as error:
        result = {'ready': False, 'error_class': type(error).__name__}
    print(json.dumps(result))
    raise SystemExit(0 if result['ready'] else 1)
