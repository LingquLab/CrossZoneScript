"""构建独立调度perf/profile候选；复核原五对象及96B ABI，不启动仿真。"""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

revision, root = REV_VALUE, Path(ROOT_VALUE)
old = Path('/opt/sim/alltoall-clock-group-8b54b467-20261117')
assert not root.exists(), 'inspect existing effects before retry'
env = json.loads((old/'environment-report.json').read_text())
def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
for path, expected in env['files'].items():
    assert digest(path) == expected, 'qualified environment changed'
root.mkdir(); source = root/'source'; source.mkdir()
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(ARCHIVE_VALUE))) as tar:
    for member in tar.getmembers():
        p = Path(member.name)
        assert not p.is_absolute() and '..' not in p.parts and not member.issym() and not member.islnk()
    tar.extractall(source)
commands = [('configure', ['cmake', '-S', str(source/'tests/collectives/memory'), '-B', str(root/'build'),
    '-DTILEXR_MEMORY_BUILD_SIMULATOR=ON', '-DASCEND_HOME_PATH=/opt/sim/Ascend/cann-9.1.0']),
    ('compile', ['cmake', '--build', str(root/'build'), '--target',
        'tilexr_alltoall_direct_two_group', 'tilexr_alltoall_direct_two_group_clock',
        'tilexr_alltoall_group_phase_sync', 'tilexr_alltoall_group_phase_clock',
        'tilexr_alltoall_direct_multi_peer', 'tilexr_alltoall_multi_phase',
        'dump_alltoall_mem_layout'] + ['tilexr_group_schedule_'+mode+'_'+trace
        for mode in ('sync','cross-first','defer-output','no-gate','local-all','local-cross','affine-all')
        for trace in ('perf','profile')] + ['-j4'])]
for stage, command in commands:
    with (root/(stage+'.log')).open('wb') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        errors = [line[:250] for line in (root/(stage+'.log')).read_text(errors='replace').splitlines()
                  if 'error:' in line][:5]
        print(json.dumps(dict(status=stage+'_failed', errors=errors)), flush=True)
        raise SystemExit(1)
layout = root/'layout.json'
layout.write_bytes(subprocess.check_output([str(root/'build/dump_alltoall_mem_layout')]))
assert json.loads(layout.read_text())['kernel_args_size'] == 96
originals = {'tilexr_alltoall_direct_two_group.o': '485359c0ef685c59e73ca266546f380e8ab2dcb70c00af114cd98be911ce453a',
             'tilexr_alltoall_direct_two_group_clock.o': '7b387948da24eb3e94bc6d7d7235f5aad1a03e3a35081662c40d0aef9d5568a4',
             'tilexr_alltoall_direct_multi_peer.o': '9cd5b2566e1abdec9fe874fe153cd93fa0a486baf3ecda9d98feaad4e7ab5e21',
             'tilexr_alltoall_group_phase_sync.o': '939da02e83cd564ddb3b1b9389e66ccb422f2a6732f1d711b627e06f407dd4f5',
             'tilexr_alltoall_group_phase_clock.o': 'aab16cda05635207af90e9a3ed29224e377f6546f50c76ccf2cf96ac21ee27c7'}
for name, expected in originals.items():
    assert digest(root/'build'/name) == expected, 'original object changed: '+name
env['source_root'] = str(source)
(root/'environment-report.json').write_text(json.dumps(env, indent=2))
base = json.loads((old/'build-clock.json').read_text())
base.update(runtime_commit=revision, source_files={str(p.relative_to(source)): digest(p)
    for p in source.rglob('*') if p.is_file()})
base['layout'].update(path=str(layout), sha256=digest(layout))
rows = []
variants = [('original','tilexr_alltoall_direct_two_group.o','direct-two-group')]
variants += [(mode+'-'+trace, 'tilexr_group_schedule_'+mode+'_'+trace+'.o',
              'direct-group-schedule-'+mode+'-'+trace)
             for mode in ('sync','cross-first','defer-output','no-gate','local-all','local-cross','affine-all')
             for trace in ('perf','profile')]
for mode, name, variant in variants:
    obj = root/'build'/name
    report = json.loads(json.dumps(base))
    report['kernel'].update(path=str(obj), sha256=digest(obj), block_dim=64, variant=variant)
    (root/('build-'+mode+'.json')).write_text(json.dumps(report, indent=2))
    rows.append(dict(mode=mode, kernel_sha256=digest(obj), bytes=obj.stat().st_size))
summary = dict(status='compiled', runtime_commit=revision, original_objects_unchanged=True, unchanged_objects=len(originals),
    source_hashes_verified=True, environment_assets=len(env['files']), records=rows, root=str(root))
(root/'build-summary.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary))
