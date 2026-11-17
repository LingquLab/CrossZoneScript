"""Transfer the SHA-verified TL prefix repair; runner-only build, no NPU launch."""
from pathlib import Path
import hashlib
import json
import subprocess


def main():
    work = Path(__file__).resolve().parent
    payload = work / 'prefix-fix04.zip'
    assert payload.stat().st_size == 107162 and hashlib.sha256(payload.read_bytes()).hexdigest() == 'd7af4a5ea1c73fa747b9f294dcdc0e972e20a81159cf593ea54e198353e6fada'
    remote = (work / 'prepare_build04.py').read_bytes()
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

    command('build04-create', ['ssh', *options, target, 'python3 - prepare'], 45, remote)
    command('build04-scp', ['scp', *options, str(payload), target + ':/root/tilelang-107-general-64ed5828-20261117-run02/runner-build04/prefix-fix04.zip'], 120)
    out = command('build04-ssh', ['ssh', *options, target, 'python3 - build'], 480, remote)
    result = json.loads(out.decode('utf-8'))
    assert result['status'] == 'PASS' and result['npu_launch'] is False
    text = json.dumps(result, ensure_ascii=True, separators=(',', ':'))
    assert len(text.encode()) < 16000
    print(text)


if __name__ == '__main__':
    main()
