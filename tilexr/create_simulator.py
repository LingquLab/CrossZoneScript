"""Create one session-owned isolated simulator container and record its identity."""
import hashlib
import json
from pathlib import Path
import platform
import subprocess


def command(*argv, timeout=45):
    return subprocess.check_output(list(argv), stderr=subprocess.PIPE, timeout=timeout).decode().strip()


image = 'tilexr-sim-9.1:aarch64-ac2cee81-v1.0.0'
expected = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
assert platform.machine() == 'aarch64'
assert command('docker', 'image', 'inspect', '--format', '{{.Id}}', image) == expected
work = Path('/root/crosszone-tilexr-acc8c509')
work.mkdir(exist_ok=False)
name = 'tilexr-matrix-acc8c509-lr8-1'
container_id = command('docker', 'run', '--pull=never', '-dt', '--name', name,
    '--label', 'crosszone.session=acc8c509-fe01-41af-a64b-ce7d45ad695e',
    '--network=none', '--runtime=ascend', '--ipc=shareable', '--shm-size=64m',
    '--ulimit', 'nofile=524288:524288', '--mount', f'type=bind,src={work},dst=/work',
    image, '/bin/bash', '-l', timeout=75)
record = {'cluster': 'tilexr-repro', 'host': 'host-94-26', 'hostname': platform.node().split('.')[0],
          'container': name, 'container_id': container_id, 'image_id': expected,
          'host_work': str(work)}
(work / 'container.json').write_text(json.dumps(record, indent=2))
probe = '''import hashlib,json
from pathlib import Path
import yaml
assert Path('/opt/sim/local/sstcore/libexec/sstsim.x').is_file()
ubus=Path('/opt/sim/plugins/libUBusSimulator.so')
assert ubus.is_file()
print(json.dumps({'cann_env_sha256':hashlib.sha256(Path('/opt/sim/cann-env.sh').read_bytes()).hexdigest(),
 'ubus_library':str(ubus),'ubus_sha256':hashlib.sha256(ubus.read_bytes()).hexdigest()}))
'''
record.update(json.loads(command('docker', 'exec', name, 'python3', '-c', probe)))
assets = []
for parent in [Path('/mnt/share/h30059441'), Path('/home/h30059441')]:
    if parent.is_dir():
        assets.extend(str(p) for p in parent.iterdir() if p.is_dir() and any(
            key in p.name.lower() for key in ['alltoall', 'tilexr', 'sst', 'matrix', 'prefill']))
record['matrix_asset_candidates'] = sorted(assets)[:30]
(work / 'container.json').write_text(json.dumps(record, indent=2))
print(json.dumps(record, ensure_ascii=True))
