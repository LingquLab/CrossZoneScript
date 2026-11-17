"""Run the audited runner-only continuation; no SCP, device query or NPU launch."""
from pathlib import Path
import hashlib
import json
import subprocess


def main():
    work = Path(__file__).resolve().parent
    remote = (work / 'resume_runner_build.py').read_bytes()
    key = Path.home() / '.ssh/id_ed25519'
    assert key.is_file()
    argv = ['ssh', '-i', str(key), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15', '-o', 'ConnectionAttempts=1',
            'root@141.61.73.107', 'python3 -']
    proc = subprocess.run(argv, input=remote, capture_output=True, timeout=480)
    (work / 'build03-ssh.stdout.log').write_bytes(proc.stdout)
    (work / 'build03-ssh.stderr.log').write_bytes(proc.stderr)
    (work / 'build03-ssh.receipt.json').write_text(json.dumps({
        'exit_code': proc.returncode, 'stdout_bytes': len(proc.stdout), 'stderr_bytes': len(proc.stderr),
        'remote_script_sha256': hashlib.sha256(remote).hexdigest()}) + '\n')
    if proc.returncode:
        raise RuntimeError('Runner continuation failed; audit saved runner-build03 evidence before retry')
    result = json.loads(proc.stdout.decode('utf-8'))
    assert result['status'] == 'PASS' and result['npu_launch'] is False and result['device_queries'] is False
    result['remote_script_sha256'] = hashlib.sha256(remote).hexdigest()
    text = json.dumps(result, ensure_ascii=True, separators=(',', ':'))
    assert len(text.encode()) < 12000
    print(text)


if __name__ == '__main__':
    main()
