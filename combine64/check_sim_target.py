# Copyright (c) 2026 TileXR Project.
"""Read a green-local deployment file and inspect its running simulator."""

import argparse
import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


class ConfigError(Exception):
    """Report only fixed messages about invalid configuration."""


class HostKeyError(Exception):
    """The SSH host key could not be verified against the configured trust."""


PRIMARY = '141.61.94.26'
DEFAULT_CONFIG = 'sst-targets.json'
PRIMARY_IMAGE = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
PRIMARY_MANIFEST = ('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/'
                    'inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549/case-manifest.json')


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    fingerprint: str
    deployment: dict


def config_path(value):
    if not isinstance(value, str) or not value:
        raise ConfigError('Provide a JSON filename or an absolute green-local path.')
    path = Path(value)
    if path.is_absolute():
        return path
    if path.name != value:
        raise ConfigError('Provide a JSON filename or an absolute green-local path.')
    return Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr' / path


def read_config(path):
    with path.open(encoding='utf-8-sig') as config:
        data = json.load(config)
    if not isinstance(data, dict) or not data:
        raise ConfigError('Expected a JSON object keyed by server address.')
    return data


def load_target(data, host):
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:%_-]*', host):
        raise ConfigError('Invalid server address.')
    item = data.get(host)
    if not isinstance(item, dict):
        raise ConfigError('The selected server is missing from the JSON file.')
    server, deployment = item.get('server'), item.get('deployment')
    if not isinstance(server, dict) or not isinstance(deployment, dict):
        raise ConfigError('The selected entry needs server and deployment objects.')
    port = server.get('port', 22)
    username, password = server.get('username'), server.get('password')
    if (server.get('host') != host or server.get('auth') != 'password' or type(port) is not int
            or not 1 <= port <= 65535 or not isinstance(username, str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+', username) or not isinstance(password, str) or not password):
        raise ConfigError('incomplete_ssh_login_configuration')
    fingerprint = server.get('host_key_sha256', '')
    if not isinstance(fingerprint, str) or (fingerprint and not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', fingerprint)):
        raise ConfigError('Use a trusted OpenSSH SHA256 host-key fingerprint.')
    fields = ('image', 'container', 'server_root', 'workspace', 'shm_size')
    if any(not isinstance(deployment.get(name), str) or not deployment[name] for name in fields):
        raise ConfigError('The deployment needs image, container, server_root, workspace and shm_size.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', deployment['container']):
        raise ConfigError('Invalid container name.')
    for name in ('server_root', 'workspace'):
        if not PurePosixPath(deployment[name]).is_absolute() or '\x00' in deployment[name]:
            raise ConfigError('The deployment paths must be absolute Linux paths.')
    size = re.fullmatch(r'([1-9][0-9]{0,15})([bkmg]?)', deployment['shm_size'].lower())
    if not size:
        raise ConfigError('Use a Docker shared-memory size such as 16g.')
    powers = {'': 0, 'b': 0, 'k': 1, 'm': 2, 'g': 3}
    requested = int(size[1]) * 1024 ** powers[size[2]]
    settings = {name: deployment[name] for name in fields}
    settings['shm_bytes'] = requested
    settings['archive_configured'] = bool(deployment.get('image_archive'))
    return Target(host, port, username, password, fingerprint, settings)


def connect(paramiko, target):
    class PinnedKey(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            digest = hashlib.sha256(key.asbytes()).digest()
            actual = 'SHA256:' + base64.b64encode(digest).decode('ascii').rstrip('=')
            if not hmac.compare_digest(actual, target.fingerprint):
                raise HostKeyError('The server key does not match host_key_sha256.')

    client = paramiko.SSHClient()
    known_hosts = Path.home() / '.ssh' / 'known_hosts'
    with client:
        if target.fingerprint:
            client.set_missing_host_key_policy(PinnedKey())
        else:
            if known_hosts.is_file():
                client.load_host_keys(str(known_hosts))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            entry = target.host if target.port == 22 else f'[{target.host}]:{target.port}'
            if client.get_host_keys().lookup(entry) is None:
                raise HostKeyError('Trusted SSH host key missing from the green account known_hosts.')
        client.connect(hostname=target.host, port=target.port, username=target.username,
                       password=target.password, look_for_keys=False, allow_agent=False,
                       timeout=10, auth_timeout=10, banner_timeout=10)
        return read_deployment(client, target.deployment)


REMOTE_CHECK = """import json, platform, subprocess
from pathlib import Path

def docker(*args):
    return subprocess.run(['docker', *args], capture_output=True, timeout=8)

def inspect(kind, name):
    result = docker(kind, 'inspect', '--', name)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None

def check(deployment):
    image = inspect('image', deployment['image'])
    container = inspect('container', deployment['container'])
    workspace = Path(deployment['workspace'])
    result = {
        'architecture': platform.machine(),
        'hostname': platform.node().split('.')[0],
        'image_id': image['Id'] if image else None,
        'container_id': container['Id'] if container else None,
        'server_root_exists': Path(deployment['server_root']).is_dir(),
        'host_workspace_exists': workspace.is_dir(),
        'image_exists': image is not None,
        'container_exists': container is not None,
    }
    if not container:
        return result
    running = container['State']['Running']
    result.update(
        running=running,
        image_matches=bool(image and container['Image'] == image['Id']),
        image_arm64=bool(image and image['Architecture'] == 'arm64'),
        shm_bytes=container['HostConfig']['ShmSize'],
    )
    mounts = []
    for mount in container['Mounts']:
        source = Path(mount['Source'])
        if mount['Type'] != 'bind' or not mount['RW'] or not source.is_absolute():
            continue
        if source in [workspace, *workspace.parents]:
            mounts.append(str(Path(mount['Destination']) / workspace.relative_to(source)))
    if running:
        name = container['Id']
        runtime_probe = (
            'import hashlib,json,os,shutil,sys; from pathlib import Path; '
            'root=Path("/opt/sim"); deployment=Path(sys.argv[1]); '
            'files=[root/"cann-env.sh",root/"sst-env.sh",root/"local/sstcore/libexec/sstsim.x",'
            'root/"plugins/libDavinciSimulatorV310.so",root/"plugins/libUBusSimulator.so"]; '
            'print(json.dumps(dict(files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() '
            'for p in files if p.is_file()}, workspace_free=shutil.disk_usage(sys.argv[2]).free '
            'if Path(sys.argv[2]).is_dir() else None, '
            'model_entries=[str(p) for folder in (root/"simulator",deployment) '
            'if folder.is_dir() for p in folder.glob("**/test_UBusSim.py")][:20])))'
        )
        try:
            probe = subprocess.run(['docker', 'exec', name, 'python3', '-c', runtime_probe,
                                    deployment['server_root'], deployment['workspace']],
                                   capture_output=True, timeout=20)
            if probe.returncode == 0:
                result['runtime_probe'] = json.loads(probe.stdout)
        except (subprocess.TimeoutExpired, ValueError):
            result['runtime_probe'] = {'status': 'probe_failed'}
        result.update(
            workspace_mounts=mounts,
            container_workspace_exists=docker('exec', name, 'test', '-d', deployment['workspace']).returncode == 0,
            cann_env=docker('exec', name, 'test', '-f', '/opt/sim/cann-env.sh').returncode == 0,
            sst_env=docker('exec', name, 'test', '-f', '/opt/sim/sst-env.sh').returncode == 0,
        )
    return result
"""


def read_deployment(client, deployment):
    code = REMOTE_CHECK + f'\nprint(json.dumps(check({deployment!r})))\n'
    stdin, stdout, _ = client.exec_command('timeout --kill-after=5 65 python3 -', timeout=75)
    stdin.write(code)
    stdin.flush()
    stdin.channel.shutdown_write()
    output = stdout.read(16385)
    if len(output) > 16384 or stdout.channel.recv_exit_status():
        raise RuntimeError('Simulator inspection failed.')
    result = json.loads(output)
    if not isinstance(result, dict):
        raise RuntimeError('Invalid simulator inspection response.')
    return result


def check_primary():
    key = Path.home() / '.ssh' / 'id_ed25519'
    remote = ('test "$(hostname -s)" = host-94-26 && test "$(uname -m)" = aarch64 && '
              f'test -r {PRIMARY_MANIFEST} && docker image inspect {PRIMARY_IMAGE} >/dev/null')
    command = ['ssh', '-T', '-i', str(key), '-p', '22', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8', 'root@' + PRIMARY, remote]
    result = subprocess.run(command, capture_output=True, timeout=20)
    return {'host': PRIMARY, 'auth': 'publickey', 'status': 'ready' if result.returncode == 0 else 'unavailable',
            'ssh_exit': result.returncode}


def check_fallback(data, host):
    try:
        target = load_target(data, host)
        import paramiko

        result = connect(paramiko, target)
        result['host'] = target.host
        result['expected_shm_bytes'] = target.deployment['shm_bytes']
        result['archive_configured'] = target.deployment['archive_configured']
        ready = (result.get('running') is True and result.get('image_matches') is True
                 and result.get('image_arm64') is True and result.get('architecture') == 'aarch64'
                 and result.get('shm_bytes', 0) >= target.deployment['shm_bytes']
                 and bool(result.get('workspace_mounts') or result.get('container_workspace_exists'))
                 and result.get('cann_env') is True and result.get('sst_env') is True)
        return {'status': 'ready' if ready else 'configuration_mismatch', **result}
    except (ConfigError, HostKeyError) as exc:
        error = str(exc)
    except Exception as exc:
        error = type(exc).__name__
    return {'host': host, 'status': 'error', 'error': error}


def select_target(request):
    host = request.get('host')
    unavailable = request.get('primary_unusable', False)
    if type(unavailable) is not bool:
        raise ConfigError('primary_unusable must be a boolean.')
    attempts = []
    if host == PRIMARY or (host is None and not unavailable):
        try:
            primary = check_primary()
        except (OSError, subprocess.TimeoutExpired) as exc:
            primary = {'host': PRIMARY, 'status': 'unavailable', 'error': type(exc).__name__}
        attempts.append(primary)
        if host == PRIMARY or primary['status'] == 'ready':
            return primary
    try:
        data = read_config(config_path(request.get('config') or DEFAULT_CONFIG))
    except FileNotFoundError:
        return {'status': 'unavailable', 'error': 'Green-local fallback configuration file missing.',
                'attempts': attempts}
    if host is not None:
        return check_fallback(data, host)
    for candidate in data:
        if candidate == PRIMARY:
            continue
        result = check_fallback(data, candidate)
        if result['status'] == 'ready':
            return {**result, 'attempts': attempts}
        attempts.append(result)
    return {'status': 'unavailable', 'attempts': attempts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--config', help='Green-local JSON filename or absolute path')
    source.add_argument('--args-file', type=Path, help='Bridge args JSON with config, host and primary_unusable')
    parser.add_argument('--host')
    parser.add_argument('--primary-unusable', action='store_true')
    parser.add_argument('--init-config', action='store_true')
    args = parser.parse_args()
    try:
        request = json.loads(args.args_file.read_text(encoding='utf-8-sig')) if args.args_file else {
            'config': args.config, 'host': args.host, 'primary_unusable': args.primary_unusable,
            'init_config': args.init_config,
        }
        setup = {}
        if request.get('init_config') is True:
            path = config_path(request.get('config') or DEFAULT_CONFIG)
            template = Path(__file__).with_name('sst-targets.example.json').read_bytes()
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open('xb') as output:
                    output.write(template)
                setup['config_created'] = True
            except FileExistsError:
                setup['config_created'] = False
            setup['config'] = path.name
        print(json.dumps({**select_target(request), **setup}))
        return 0
    except ConfigError as exc:
        error = str(exc)
    except Exception as exc:
        error = type(exc).__name__
    print(json.dumps({'status': 'error', 'error': error}))
    return 1


if __name__ == '__main__':
    sys.exit(main())
