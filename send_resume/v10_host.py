# Copyright (c) 2026 TileXR Project.
"""Create and stop one session-owned, network-isolated V1.0 simulator comparison."""

import json
from pathlib import Path
import socket
import subprocess

IMAGE = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
SESSION = '7de11bac-47e4-498d-b107-e746122e3b0a'
NAME = 'tilexr-send-7de11bac-v10'
WORK = Path('/var/tmp/tilexr-send-7de11bac-v10')
OLD = Path('/var/tmp/tilexr-combine-ht-500e2a93-b84e-4d36-a9aa-8d2cd0321cdf-v11')
SOURCE = '__CONTAINER_SOURCE__'


def call(*args, timeout=30):
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    assert proc.returncode == 0, 'container_command_failed'
    return proc.stdout


def main():
    assert socket.gethostname().split('.')[0] == 'host-94-26'
    assert call('docker', 'image', 'inspect', '--format', '{{.Id}} {{.Os}}/{{.Architecture}}', IMAGE).strip() == IMAGE+' linux/arm64'
    existing = subprocess.run(['docker', 'inspect', NAME], capture_output=True, timeout=20)
    assert existing.returncode != 0, 'comparison_container_already_exists_read_evidence'
    WORK.mkdir(exist_ok=False)
    (WORK / 'trial.py').write_text(SOURCE)
    identifier = call('docker', 'run', '-dt', '--name', NAME, '--label', 'crosszone.session='+SESSION,
        '--network=none', '--runtime=ascend', '--ipc=shareable', '--shm-size=64m',
        '--ulimit', 'nofile=524288:524288', '--mount', 'type=bind,src='+str(OLD)+',dst=/work,readonly',
        '--mount', 'type=bind,src='+str(WORK)+',dst=/trial', IMAGE, '/bin/bash', '-l', timeout=60).strip()
    try:
        info = json.loads(call('docker', 'inspect', '--format', '{{json .}}', NAME))
        assert info['Id'] == identifier and info['Image'] == IMAGE
        assert info['Config']['Labels']['crosszone.session'] == SESSION
        assert info['HostConfig']['NetworkMode'] == 'none'
        assert any(m['Destination']=='/work' and not m['RW'] for m in info['Mounts'])
        proc = subprocess.run(['docker', 'exec', NAME, 'python3', '-B', '/trial/trial.py'], capture_output=True, timeout=850)
        (WORK / 'trial.stdout').write_bytes(proc.stdout)
        (WORK / 'trial.stderr').write_bytes(proc.stderr)
        result = json.loads(proc.stdout)
        result.update(trial_exit_code=proc.returncode, host_report=str(WORK), container_id=identifier, image_id=IMAGE)
    finally:
        call('docker', 'stop', '-t', '15', NAME, timeout=35)
    result['container_stopped'] = call('docker', 'inspect', '--format', '{{.State.Running}}', NAME).strip() == 'false'
    assert result['container_stopped']
    (WORK / 'host-result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['ok'] and result['trial_exit_code'] == 0 else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps(dict(ok=False,error_type=type(error).__name__,detail=str(error)[:300])))
        raise SystemExit(1)
