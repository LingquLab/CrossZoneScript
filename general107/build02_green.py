"""Transfer the small dependency repair; build once in a fresh experiment root."""
from pathlib import Path
import hashlib
import json
import subprocess


def main():
    work = Path(__file__).resolve().parent
    payload = work / 'build02-overlay.zip'
    assert payload.stat().st_size == 356509 and hashlib.sha256(payload.read_bytes()).hexdigest() == 'a85ea076e4ab51a6f295a4eec0fe68c7bd1d587b19bad0582de90e8769cd6801'
    remote = (work / 'prepare_build02.py').read_bytes()
    key = Path.home() / '.ssh/id_ed25519'; assert key.is_file()
    options = ['-i', str(key), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15', '-o', 'ConnectionAttempts=1']
    target = 'root@141.61.73.107'

    def command(label, argv, timeout, data=None):
        process = subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
        (work / (label + '.stdout.log')).write_bytes(process.stdout)
        (work / (label + '.stderr.log')).write_bytes(process.stderr)
        (work / (label + '.receipt.json')).write_text(json.dumps({'exit_code': process.returncode, 'stdout_bytes': len(process.stdout), 'stderr_bytes': len(process.stderr)}) + '\n')
        if process.returncode:
            raise RuntimeError(label + ' failed; read saved state before retry')
        return process.stdout

    command('build02-create', ['ssh', *options, target, 'python3 - prepare'], 45, remote)
    command('build02-scp', ['scp', *options, str(payload), target + ':/root/tilelang-107-general-64ed5828-20261117-run02/build02-overlay.zip'], 120)
    out = command('build02-ssh', ['ssh', *options, target, 'python3 - build'], 900, remote)
    result = json.loads(out.decode('utf-8'))
    assert result['status'] == 'PASS' and result['npu_launch'] is False
    text = json.dumps(result, ensure_ascii=True, separators=(',', ':'))
    assert len(text.encode()) < 16000
    print(text)


if __name__ == '__main__':
    main()
