"""Execute the fixed ABI3 capture entry; keep SSH output out of Bridge logs."""
from pathlib import Path
import hashlib
import json
import subprocess


def main():
    work = Path(__file__).resolve().parent
    remote = (work / 'capture_remote.py').read_bytes()
    key = Path.home() / '.ssh/id_ed25519'
    assert key.is_file()
    options = ['-i', str(key), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15', '-o', 'ConnectionAttempts=1']
    process = subprocess.run(['ssh', *options, 'root@141.61.73.107', 'python3 -'],
                             input=remote, capture_output=True, timeout=3900)
    (work / 'capture-ssh.stdout.log').write_bytes(process.stdout)
    (work / 'capture-ssh.stderr.log').write_bytes(process.stderr)
    (work / 'capture-ssh.receipt.json').write_text(json.dumps({
        'exit_code': process.returncode, 'stdout_bytes': len(process.stdout),
        'stderr_bytes': len(process.stderr), 'remote_script_sha256': hashlib.sha256(remote).hexdigest()}) + '\n')
    if process.returncode:
        raise RuntimeError('Capture failed; audit saved state before any device retry')
    result = json.loads(process.stdout.decode('utf-8'))
    assert result['status'] == 'PASS' and result['fresh_rank_groups'] == 10
    text = json.dumps(result, ensure_ascii=True, separators=(',', ':'))
    assert len(text.encode()) < 12000
    print(text)


if __name__ == '__main__':
    main()
