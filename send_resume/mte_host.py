# Copyright (c) 2026 TileXR Project.
"""Check the retained container, run one isolated MTE trial, and export its result."""

import hashlib
import json
from pathlib import Path
import re
import socket
import struct
import subprocess

WORK = Path('/var/tmp/tilexr-combine-ht-500e2a93-b84e-4d36-a9aa-8d2cd0321cdf-v11')
NAME = 'tilexr-combine-ht-500e2a93-v11'
TRIAL_SOURCE = '__TRIAL_SOURCE__'
TRIAL_NAME = 'mte-a1'
REQUIRE_SCALED_DOORBELL = False


def call(*argv):
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=25)
    assert proc.returncode == 0, 'container_query_failed'
    return proc.stdout


def active_sst():
    rows = call('docker', 'top', NAME, '-eo', 'pid,comm,stat').splitlines()[1:]
    return any(row.split()[1] in ('sst', 'sstsim.x') and not row.split()[2].startswith('Z') for row in rows)


def main():
    assert socket.gethostname().split('.')[0] == 'host-94-26'
    info = json.loads(call('docker', 'inspect', '--format', '{{json .}}', NAME))
    assert info['Id'] == 'e9ab0e97f6b31670b6ce68412fdf20c15203502b9aa8fc0840d45e927a0254a4'
    assert info['Image'] == 'sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f'
    assert info['State']['Running'] and info['HostConfig']['NetworkMode'] == 'none'
    assert any(m['Source'] == str(WORK) and m['Destination'] == '/work' for m in info['Mounts'])
    assert not active_sst(), 'existing_sst_is_active'
    assert hashlib.sha256((WORK / 'tilexr_moonep_combine_ht_kernel.o').read_bytes()).hexdigest() == '2e31f0e06fe776f0a0feb5232f6c9593e3ca453a33e7a5983176dc8b7b7883fd'
    folder = WORK / 'resume-7de1'
    folder.mkdir(exist_ok=True)
    prior_errors = []
    if REQUIRE_SCALED_DOORBELL:
        saved = folder / 'manual-a1/send-only'
        addresses = []
        with (saved / 'sim.log').open(errors='replace') as handle:
            for line in handle:
                match = re.search(r'FATAL.*?Unknown low level mem to access!\[addr=\s*(\d+)', line)
                if match:
                    addresses.append(int(match[1]))
                    prior_errors.append(line[match.start():match.start()+700].strip())
        contexts = (saved / 'input_bin/rank0/sq_ctx.bin').read_bytes()
        doorbells = [struct.unpack_from('<Q', contexts, (peer*40+qp)*80+48)[0]
                     for peer in range(8) for qp in range(8)]
        assert any(addr == db*8 for addr in addresses for db in doorbells), 'prior_failure_not_scaled_doorbell'
    assert not (folder / TRIAL_NAME).exists(), 'trial_already_attempted_read_saved_evidence'
    script = folder / (TRIAL_NAME + '.py')
    with script.open('x') as handle:
        handle.write(TRIAL_SOURCE)
    proc = subprocess.run(['docker', 'exec', NAME, 'python3', '/work/resume-7de1/' + script.name],
                          capture_output=True, timeout=570)
    (folder / (TRIAL_NAME + '.stdout')).write_bytes(proc.stdout)
    (folder / (TRIAL_NAME + '.stderr')).write_bytes(proc.stderr)
    result = json.loads(proc.stdout)
    if prior_errors:
        result['prior_manual_errors'] = prior_errors[:2]
    result['trial_exit_code'] = proc.returncode
    result['no_sst_active'] = not active_sst()
    assert len(proc.stdout) < 25000, 'result_too_large'
    assert result['no_sst_active'], 'trial_process_still_active'
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['ok'] and proc.returncode == 0 else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps(dict(ok=False, error_type=type(error).__name__, detail=str(error)[:250])))
        raise SystemExit(1)
