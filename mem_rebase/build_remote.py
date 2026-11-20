"""Bind fresh CANN builds and ABI layout to a source commit and qualified SST environment."""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

args, root = ARGS_VALUE, Path(ROOT_VALUE)
revision = args['revision']
old = Path('/opt/sim/alltoall-group-schedule-347747af-20261118')
env = json.loads((old / 'environment-report.json').read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


for name, expected in env['files'].items():
    assert digest(name) == expected, 'qualified environment asset changed'
root.mkdir()
source = root / 'source'
source.mkdir()
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(ARCHIVE_VALUE))) as tar:
    for member in tar.getmembers():
        path = Path(member.name)
        assert not path.is_absolute() and '..' not in path.parts
        assert member.isdir() or member.isfile()
    tar.extractall(source)

build = root / 'build'
variants = [
    ('memory', 'tilexr_alltoall_mem_sim', 'memory', None),
    ('group', 'tilexr_alltoall_direct_two_group', 'direct-two-group', 64),
    ('clock', 'tilexr_alltoall_direct_two_group_clock', 'direct-two-group-clock', 64),
]
variants += [('multi-b'+str(n), 'tilexr_alltoall_direct_multi_peer', 'direct-multi-peer', n)
             for n in (1, 2, 4, 8, 16, 32)]
variants += [(mode+'-'+trace, 'tilexr_group_schedule_'+mode+'_'+trace,
              'direct-group-schedule-'+mode+'-'+trace, 64)
             for mode in ('sync', 'cross-first', 'defer-output', 'no-gate', 'local-all',
                          'local-cross', 'affine-all') for trace in ('perf', 'profile')]
extra = ['tilexr_alltoall_mem_profile', 'tilexr_alltoall_group_phase_sync',
         'tilexr_alltoall_group_phase_clock', 'tilexr_alltoall_multi_phase']
targets = sorted({row[1] for row in variants} | set(extra) | {'dump_alltoall_mem_layout'})
commands = {
    'configure': ['cmake', '-S', str(source/'tests/collectives/memory'), '-B', str(build),
                  '-DTILEXR_MEMORY_BUILD_SIMULATOR=ON', '-DTILEXR_MEMORY_BUILD_PROFILE=ON',
                  '-DASCEND_HOME_PATH=/opt/sim/Ascend/cann-9.1.0'],
    'compile': ['cmake', '--build', str(build), '--target'] + targets + ['-j4'],
}
for stage, command in commands.items():
    log_path = root/(stage+'.log')
    with log_path.open('wb') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    if result.returncode:
        errors = [line.strip()[:320] for line in log_path.read_text(errors='replace').splitlines()
                  if 'error:' in line.lower()][:10]
        print(json.dumps(dict(status=stage+'_failed', runtime_commit=revision, errors=errors,
                              returncode=result.returncode)))
        raise SystemExit(1)

layout_path = root/'layout.json'
layout_path.write_bytes(subprocess.check_output([str(build/'dump_alltoall_mem_layout')], timeout=15))
layout = json.loads(layout_path.read_text())
assert layout['kernel_args_size'] == 96 and layout['max_ranks'] >= 64
env['source_root'] = str(source)
(root/'environment-report.json').write_text(json.dumps(env, indent=2))
files = {str(path.relative_to(source)): digest(path) for path in source.rglob('*') if path.is_file()}
objects = {name: dict(sha256=digest(build/(name+'.o')), bytes=(build/(name+'.o')).stat().st_size)
           for name in targets if name != 'dump_alltoall_mem_layout'}
for key, name, variant, blocks in variants:
    obj = objects[name]
    kernel = dict(path=str(build/(name+'.o')), sha256=obj['sha256'], entry='TileXRAllToAllMemSim',
                  variant=variant)
    if blocks is not None:
        kernel['block_dim'] = blocks
    report = dict(state='compiled_and_abi_verified_not_simulated', runtime_commit=revision,
                  source_files=files, layout=dict(path=str(layout_path), sha256=digest(layout_path)),
                  kernel=kernel)
    (root/('build-'+key+'.json')).write_text(json.dumps(report, indent=2))
summary = dict(status='compiled', runtime_commit=revision, main=args['main'],
               environment_assets=len(env['files']), layout_sha256=digest(layout_path),
               comm_args_size=layout['comm_args_size'], kernel_args_size=layout['kernel_args_size'],
               root=str(root), objects=objects, source_files=len(files), simulation_launched=False)
(root/'build-summary.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary))
