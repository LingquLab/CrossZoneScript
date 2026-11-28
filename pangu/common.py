"""Bounded Windows-to-Linux operations for this session's offline deployment."""
import hashlib
import json
import pathlib
import shlex
import shutil
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
REPO = pathlib.Path('D:/cross_zone/pangu_sophon_pytorch')
LOGS = pathlib.Path.cwd() / 'pangu-operation-logs'
LOGS.mkdir(exist_ok=True)
ALIASES = ('ma-0', 'ma-1', 'ma-2')


def run(argv, label, *, timeout=120, data=None):
    completed = subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
    (LOGS / (label + '.stdout')).write_bytes(completed.stdout)
    (LOGS / (label + '.stderr')).write_bytes(completed.stderr)
    if completed.returncode:
        raise RuntimeError(f'{label} exited {completed.returncode}; logs retained in {LOGS}')
    return completed.stdout.decode('utf-8', errors='strict').strip()


def ssh_script(alias, name, *args, timeout=120):
    assert alias in ALIASES
    ssh = shutil.which('ssh')
    assert ssh, 'SSH client missing'
    command = shlex.join(['python3', '-', *map(str, args)])
    result = run([ssh, '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                  '-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',alias, command],
                 alias + '-' + pathlib.Path(name).stem, timeout=timeout,
                 data=(HERE / name).read_bytes())
    return json.loads(result)


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def report(name, value):
    (LOGS / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(value, ensure_ascii=False))
