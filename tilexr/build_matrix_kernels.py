"""Build four guide-supported kernels with stage profiling in new directories."""
import json
import os
from pathlib import Path
import subprocess
import sys


root = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr'
workspace = json.loads((root / 'workspace.json').read_text(encoding='utf-8'))
baseline = root / 'reports/lr8-2'
request = json.loads((baseline / 'current-request.json').read_text(encoding='utf-8'))
assert request['runtime_commit'] == '85afd1fa61669a5ef800a80bec4df93745f119c6'
assert request['container'] == 'tilexr-matrix-acc8c509-lr8-1'
report = root / 'reports/matrix-build-1'
report.mkdir(parents=True, exist_ok=False)
remote = r'''import json,subprocess
from pathlib import Path
r={'scope':'directory names only; retained matrix assets'}
p=subprocess.run(['timeout','55','find','/mnt/share','-maxdepth','3','-type','d','(',
 '-iname','*alltoall*','-o','-iname','*prefill*','-o','-iname','*sst*','-o','-iname','*matrix*',')'],
 capture_output=True,timeout=60)
paths=[line for line in p.stdout.decode(errors='replace').splitlines() if len(line)<500]
r['share_search_exit_code']=p.returncode
r['share_candidates']=sorted(paths)[:100]
p=Path('/home/h30059441')
r['home_candidates']=sorted(str(item) for item in p.iterdir() if item.is_dir() and any(
 word in item.name.lower() for word in ['alltoall','prefill','sst','matrix']) and 'simt' not in item.name.lower())[:50] if p.is_dir() else []
print(json.dumps(r))
'''
p = subprocess.run(['ssh', '-T', '-i', 'C:/Users/z00813871/.ssh/id_ed25519',
    '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ConnectTimeout=12', 'root@141.61.94.26', 'timeout 75 python3 -'],
    input=remote.encode(), capture_output=True, timeout=90)
if p.returncode:
    raise RuntimeError('Targeted matrix asset discovery failed')
assets = json.loads(p.stdout)
(report / 'asset-discovery.json').write_text(json.dumps(assets, indent=2), encoding='utf-8')
names = ('cluster', 'host', 'hostname', 'container', 'container_id', 'image_id', 'sources',
         'runtime_commit', 'source_lock_sha256', 'cann_root', 'driver_root', 'cann_env',
         'cann_env_sha256', 'ubus_library', 'ubus_sha256', 'sim_variant')
cli = [sys.executable, str(Path(workspace['checkouts']['tool']) / 'scripts/run_alltoall_sim_kernel.py')]
summary = {'stage_profiling': 1, 'runtime_commit': request['runtime_commit'], 'kernels': [], 'asset_discovery': assets}
for kernel in ['urma_lr8', 'urma_cw', 'urma_lr8_p16_s16_local7', 'urma_lr8_p16_s8_local7']:
    local = report / kernel
    local.mkdir()
    stage = '/work/matrix-build-1/' + kernel
    common = ['--inventory', workspace['inventory'], '--output', stage + '/kernel', '--timeout', '450']
    for name in names:
        common += ['--' + name.replace('_', '-'), request[name]]
    states = {}
    phases = [('build', ['--kernel-name', kernel, '--stage-profiling', '1'], 'verified_not_simulated'),
              ('abi', [], 'host_abi_verified_not_simulated'),
              ('inspect', ['--library-dir', '/opt/sim/plugins', '--inspection-output', stage + '/environment'],
               'resources_verified_not_simulated')]
    for phase, extra, expected in phases:
        (report / 'progress.json').write_text(json.dumps({'kernel': kernel, 'phase': phase, 'state': 'running'}), encoding='utf-8')
        with (local / (phase + '.log')).open('wb') as log:
            p = subprocess.run(cli + [phase] + common + extra + ['--local-output', str(local / phase)],
                               stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=495)
        if p.returncode:
            (report / 'progress.json').write_text(json.dumps({'kernel': kernel, 'phase': phase,
                'state': 'failed', 'exit_code': p.returncode}), encoding='utf-8')
            raise RuntimeError('Matrix qualification failed; inspect saved phase report')
        value = json.loads((local / phase / 'result.json').read_text(encoding='utf-8'))
        assert value['state'] == expected
        states[phase] = value['state']
    current = json.loads((local / 'build/request.json').read_text(encoding='utf-8'))
    current['environment_report'] = stage + '/environment'
    (local / 'current-request.json').write_text(json.dumps(current, indent=2), encoding='utf-8')
    summary['kernels'].append({'kernel': kernel, 'report': str(local), 'phases': states})
    (report / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
(report / 'progress.json').write_text(json.dumps({'state': 'passed', 'completed_kernels': 4}), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=True))
