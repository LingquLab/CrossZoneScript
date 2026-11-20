"""Create this session's isolated CPU-only container and inspect simulator binaries."""
import glob
import json
from pathlib import Path
import subprocess

NAME = 'tilexr-cpu-b4f4660a-lr8'
IMAGE = 'tilexr-sim-9.1:aarch64-ac2cee81-v1.0.0'
EXPECTED = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
WORK = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54')


def command(argv, timeout=30):
    return subprocess.check_output(argv, stderr=subprocess.PIPE, timeout=timeout).decode('utf-8')


assert command(['docker','image','inspect',IMAGE,'--format','{{.Id}}']).strip() == EXPECTED
probe = subprocess.run(['docker','container','inspect',NAME], capture_output=True, timeout=15)
assert probe.returncode != 0, 'Task container already exists; inspect before continuing'
WORK.mkdir(parents=True, exist_ok=True)
cid = command(['docker','create','--name',NAME,'--runtime=runc','--network=none',
    '--cap-drop=ALL','--security-opt=no-new-privileges','--cpus=8','--memory=16g',
    '--pids-limit=1024','--shm-size=64m','--ulimit','nofile=524288:524288',
    '--mount','type=bind,src='+str(WORK)+',dst=/work',IMAGE,'/bin/bash','-lc','exec sleep infinity']).strip()
command(['docker','start',NAME])
info, = json.loads(command(['docker','inspect',NAME]))
assert info['Image'] == EXPECTED and info['HostConfig']['Runtime'] == 'runc'
assert not info['HostConfig']['Privileged'] and not info['HostConfig']['Devices']
assert info['HostConfig']['NetworkMode'] == 'none'
inspect = '''import glob,hashlib,json,pathlib,shutil
p=pathlib.Path
devices=glob.glob('/dev/davinci*')+glob.glob('/dev/devmm*')+glob.glob('/dev/hisi_hdc*')
assert not devices, 'NPU device unexpectedly visible'
paths=['/opt/sim/cann-env.sh','/opt/sim/plugins/libUBusSimulator.so','/opt/sim/local/sstcore/libexec/sstsim.x','/opt/sim/Ascend/cann-9.1.0/tools/bisheng_compiler/bin/bisheng','/usr/local/Ascend/driver/kernel/inc']
records=[{'path':s,'exists':p(s).exists(),'sha256':hashlib.sha256(p(s).read_bytes()).hexdigest() if p(s).is_file() and s.endswith(('.sh','.so')) else None} for s in paths]
print(json.dumps({'devices':devices,'paths':records,'python_yaml':__import__('importlib.util').util.find_spec('yaml') is not None,'host_cxx':shutil.which('c++'),'sim_top_level':sorted(x.name for x in p('/opt/sim').iterdir()) if p('/opt/sim').is_dir() else []}))
'''
result = {'container':NAME,'container_id':cid,'image_id':EXPECTED,'runtime':'runc','host_work':str(WORK),
          'cpu_only_inspection':json.loads(command(['docker','exec',NAME,'python3','-c',inspect]))}
WORK.joinpath('container.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,ensure_ascii=True))
