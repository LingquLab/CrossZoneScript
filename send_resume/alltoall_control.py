# Copyright (c) 2026 TileXR Project.
"""Run one maintained AllToAll control to compare its actual scalar doorbell path."""

import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, '/work')
import sst

folder = Path('/work/resume-7de1/alltoall-control-a1')
folder.mkdir(exist_ok=False)
experiment = Path('/work/tooling/tools/unitest/simulator/experiments/alltoall_udma')
snapshot = json.loads(Path('/work/environment.snapshot.json').read_text())
sst.verify_environment(snapshot)
env_file = '/work/perf-environment.json'
build = ['timeout', '--kill-after=10', '200', 'python3', str(experiment / 'kernel_build.py'),
    '--env-config', env_file, '--output-dir', str(folder / 'build')]
with (folder / 'build.log').open('wb') as log:
    built = subprocess.run(sst.shell_command(snapshot['settings'], 'cann', build), stdout=log, stderr=subprocess.STDOUT, timeout=225)
assert built.returncode == 0, 'control_build_failed'
binary = folder / 'build/tilexr_alltoall_a5_kernel.o'
generate = ['timeout', '--kill-after=15', '270', 'python3', str(experiment / 'generate.py'),
    '--env-config', env_file, '--rank-size', '8', '--elements-per-peer', '16', '--seed', '2026',
    '--output-dir', str(folder / 'case'), '--kernel-file', str(binary),
    '--build-manifest', str(folder / 'build/build_manifest.json'), '--log-level', '1',
    '--instruction-dump-chips', '0', '--run', '--threads', '8', '--timeout', '180']
with (folder / 'generate.log').open('wb') as log:
    generated = subprocess.run(sst.shell_command(snapshot['settings'], 'sst', generate), stdout=log, stderr=subprocess.STDOUT, timeout=295)
reports = sorted((folder / 'case').rglob('run.json'))
assert reports, 'control_run_report_missing'
saved = json.loads(reports[-1].read_text())
result = dict(ok=True, diagnostic_only=True, candidate_send_verified=False, control_generator_exit=generated.returncode,
    control_kernel_sha256=sst.checksum(binary), report=str(folder),
    control_run={k: v for k, v in saved.items() if isinstance(v, (bool, int, float, str, list)) and k not in ('command', 'arguments')},
    instruction_samples=[], fatal=[])
for path in sorted((folder / 'case').rglob('*sudetail.dump')):
    previous = []
    with path.open(errors='replace') as handle:
        for line in handle:
            if 'scalarStDev' in line:
                result['instruction_samples'].append(dict(path=str(path), lines=previous[-3:] + [line.strip()[:900]]))
                break
            previous.append(line.strip()[:900])
            if len(previous) > 3:
                previous.pop(0)
    if len(result['instruction_samples']) >= 2:
        break
for path in sorted((folder / 'case').rglob('sim.log'))[-1:]:
    with path.open(errors='replace') as handle:
        for line in handle:
            match = re.search(r'FATAL|Unknown low level|[Aa]ssertion|Segmentation fault', line)
            if match and len(result['fatal']) < 2:
                result['fatal'].append(line[max(0,match.start()-30):match.start()+700].strip())
(folder / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, ensure_ascii=True))
