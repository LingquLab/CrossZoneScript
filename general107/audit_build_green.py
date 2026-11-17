"""Audit the failed build's saved local/remote state without replaying work."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess

SUBJECT = 'tl-a5-107-general-abi3-build-20261117-01'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def log_summary(path):
    if not path.is_file():
        return {'exists': False}
    raw = path.read_bytes()
    lines = raw.decode('utf-8', errors='replace').splitlines()
    classes = sorted(set(re.findall(r'\b[A-Za-z]+(?:Error|Exception)\b', '\n'.join(lines))))
    markers = {s: any(s in line for line in lines) for s in ('general-create failed', 'general-scp failed', 'general-build-ssh failed', 'Authentication failed', 'Permission denied', 'Connection timed out', 'Host key verification failed', 'No such file')}
    return {'exists': True, 'bytes': len(raw), 'sha256': digest(raw), 'error_classes': classes, 'markers': markers}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--args-file', required=True)
    args = parser.parse_args()
    execution = Path(args.args_file).resolve().parent
    assert execution.name == 'execution'
    runs = execution.parents[2]
    assert runs.name == 'runs'
    previous = runs / SUBJECT / '1' / 'execution'
    assert previous.is_dir(), 'Subject execution not found'
    source = previous / 'source/general107'
    result = {'schema': 'tilelang_general_107_build_audit.v1', 'subject_task': SUBJECT,
              'read_only': True, 'npu_launch': False, 'device_queries': False,
              'saved_execution': json.loads((previous / 'execution.json').read_text()),
              'local_logs': {}, 'local_receipts': {}}
    for name in ('stdout.log', 'stderr.log'):
        result['local_logs'][name] = log_summary(previous / name)
    for label in ('general-create', 'general-scp', 'general-build-ssh'):
        receipt = source / (label + '.receipt.json')
        result['local_receipts'][label] = json.loads(receipt.read_text()) if receipt.exists() else {'exists': False}
        for suffix in ('.stdout.log', '.stderr.log'):
            result['local_logs'][label + suffix] = log_summary(source / (label + suffix))
    remote = Path(__file__).with_name('read_build_saved.py').read_bytes()
    result['remote_script_sha256'] = digest(remote)
    key = Path.home() / '.ssh/id_ed25519'
    command = ['ssh', '-i', str(key), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15', '-o', 'ConnectionAttempts=1',
               'root@141.61.73.107', 'python3 -']
    try:
        proc = subprocess.run(command, input=remote, capture_output=True, timeout=150)
        (execution / 'build-audit-ssh.stdout.log').write_bytes(proc.stdout)
        (execution / 'build-audit-ssh.stderr.log').write_bytes(proc.stderr)
        result['ssh_exit'] = proc.returncode
        result['ssh_stderr_bytes'] = len(proc.stderr)
        if proc.returncode == 0:
            result['remote'] = json.loads(proc.stdout.decode('utf-8'))
        else:
            result['ssh_error_summary'] = log_summary(execution / 'build-audit-ssh.stderr.log')
    except subprocess.TimeoutExpired:
        result['ssh_timed_out'] = True
    text = json.dumps(result, ensure_ascii=True, separators=(',', ':'))
    assert len(text.encode()) < 16000
    print(text)


if __name__ == '__main__':
    main()
