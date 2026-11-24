# Copyright (c) 2026 TileXR Project.
"""Compare the same scalar Send object with the installed complete PV model set."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, '/work')
import sst

folder = Path('/work/resume-7de1/pv-a1')
folder.mkdir(exist_ok=False)
old = json.loads(Path('/work/environment.snapshot.json').read_text())
sst.verify_environment(old)
result = dict(ok=True, diagnostic_only=True, perf_send_pass=False, pv_send_pass=False,
              report=str(folder), mmio_symbols=[])
for library in ('libDavinciSimulator.so', 'libDavinciSimulatorV310.so'):
    path = Path(old['settings']['library_dir']) / library
    proc = subprocess.run(['nm', '-D', '-C', str(path)], capture_output=True, text=True, timeout=30)
    symbols = [line.split(' ',2)[-1][:250] for line in proc.stdout.splitlines()
        if re.search(r'stdev|st_dev|LsuL1::.*(?:[Aa]ddr|[Dd]ev|[Ii]ssu|[Dd]ecod)',line,re.I)]
    result['mmio_symbols'].append(dict(library=str(path), exit_code=proc.returncode, names=symbols[:16]))
config = json.loads(Path(old['settings']['config_file']).read_text())
pv = Path('/opt/sim/simulator/v310_deployment/config/pv_config')
for target in sst.RESOURCE_GROUPS:
    if target.startswith('v310_config/'):
        source = pv / ('pv_simulator_config.yml' if target.endswith('/davidV100_simulator_config.yml') else Path(target).name)
        assert source.is_file(), 'complete_pv_resource_missing'
        config['platform_resources'][target] = str(source)
env_file = folder / 'environment.json'
env_file.write_text(json.dumps(config,indent=2)+'\n')
env = sst.load_environment(env_file)
sources = {key:Path(value) for key,value in env['platform_resources'].items()}
snapshot = dict(settings=env, resources={key:sst.file_record(path) for key,path in sources.items()},
                runtime_files=sst.runtime_fingerprint(env,sources))
(folder/'environment.snapshot.json').write_text(json.dumps(snapshot,indent=2)+'\n')
sst.verify_environment(snapshot)
binary = Path('/work/resume-7de1/mte-a1/tilexr_moonep_combine_ht_kernel.o')
assert sst.checksum(binary)=='efe602e3973951d3059c3af8ff371166140a2dfb2687333d71b1deedb7288a36'
sst.KERNEL, sst.WORK = binary, folder
specs,routes=sst.fixtures.prepare(8,4,256,16,4,40,64)
result['route_sha256']=hashlib.sha256(json.dumps(routes,separators=(',',':')).encode()).hexdigest()
assert result['route_sha256']=='f5eb786c6ca0a725607b16caef0ac8a8c0efc161576b185d9c74ffa2f4127881'
send=sst.run_phase(folder/'send-only',1,specs,[spec.serialize() for spec in specs],snapshot,
    dict(mode='send',threads=8,stage_timeout=240))
result.update(kernel_sha256=sst.checksum(binary), sim_send=True, pv_send_pass=send['ok'],
    model=send['model']['su'], send={key:value for key,value in send.items() if key not in ('model','verified_workspace_sha256')}, fatal=[])
with (folder/'send-only/sim.log').open(errors='replace') as handle:
    for line in handle:
        match=re.search(r'FATAL|Unknown low level|[Aa]ssertion|Segmentation fault',line)
        if match and len(result['fatal'])<2:
            result['fatal'].append(line[max(0,match.start()-30):match.start()+700].strip())
result['ok']=not send['timed_out']
(folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=True))
raise SystemExit(0 if result['ok'] else 1)
