"""Run one pinned four-variant fixed-P2 phase and preserve child output under distinct names."""
from pathlib import Path
import datetime
import hashlib
import json
import subprocess
import time

ROOT = '/root/tilelang-107-targeted-p2-20261117-run01'


def run(phase):
    assert phase in ('correctness', 'capture')
    work = Path(__file__).resolve().parent
    remote = (work / 'phase_remote.py').read_bytes()
    assert b"BUILD_SHA='PENDING_RECEIPT'" not in remote and b"RUNTIME_SHA='PENDING_RUNTIME'" not in remote, 'Build/runtime receipts must be pinned first'
    key = Path.home() / '.ssh/id_ed25519'
    assert key.is_file()
    options = ['-i', str(key), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15',
               '-o', 'ConnectionAttempts=1']
    target = 'root@141.61.73.107'

    def command(label, argv, timeout, data=None):
        started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        clock = time.monotonic()
        try:
            process = subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
            out, err, code, expired = process.stdout, process.stderr, process.returncode, False
        except subprocess.TimeoutExpired as error:
            out, err, code, expired = error.stdout or b'', error.stderr or b'', None, True
        (work / (label + '.stdout.log')).write_bytes(out)
        (work / (label + '.stderr.log')).write_bytes(err)
        receipt = {'started_utc': started,
                   'finished_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'seconds': round(time.monotonic() - clock, 3), 'exit_code': code,
                   'timed_out': expired, 'stdout_bytes': len(out), 'stderr_bytes': len(err),
                   'remote_script_sha256': hashlib.sha256(data).hexdigest() if data else None}
        (work / (label + '.receipt.json')).write_text(json.dumps(receipt) + '\n')
        if expired or code != 0:
            raise RuntimeError(label + ' failed or timed out; audit saved state before any replay')
        return out

    if phase == 'correctness':
        prepared = command('targetedp2-runtime-prepare', ['ssh', *options, target, 'python3 - prepare'], 60, remote)
        assert json.loads(prepared)['status'] == 'PREPARED'
        command('targetedp2-runtime-scp', ['scp', *options, str(work / 'runtime.zip'),
                                      target + ':' + ROOT + '/runtime-support01/runtime.zip'], 120)
    output = command('targetedp2-' + phase, ['ssh', *options, target, 'python3 - ' + phase],
                     4100 if phase == 'correctness' else 13400, remote)
    result = json.loads(output.decode('utf-8'))
    count = 12 if phase == 'correctness' else 40
    assert result['status'] == 'PASS' and result['fresh_rank_groups'] == count
    assert result['variants'] == ['B', 'U', 'R64', 'R128'] and result['groups_in_remote_parent'] == count
    assert result['phase'] == phase and result['npu_launch'] is True
    assert result['cores'] == [64, 64] and result['original_configuration_preserved'] is True
    text = json.dumps(result, ensure_ascii=True, separators=(',', ':'))
    assert len(text.encode('utf-8')) < 14000
    print(text)
