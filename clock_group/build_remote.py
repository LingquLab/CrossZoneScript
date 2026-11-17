"""容器内核验既有仿真资产、构建精确归档并保留源文件和对象摘要；不启动仿真。"""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

revision = REV_VALUE
root = Path(ROOT_VALUE)
old = Path('/opt/sim/alltoall-rebase-db76effb-20261116')
assert not root.exists(), 'preserve prior build state; inspect before retry'
env = json.loads((old/'environment-report.json').read_text())
def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
for path, sha in env['files'].items():
    assert digest(path) == sha, 'qualified asset changed'
root.mkdir(); source = root/'source'; source.mkdir()
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(ARCHIVE_VALUE))) as tar:
    for member in tar.getmembers():
        p = Path(member.name)
        assert not p.is_absolute() and '..' not in p.parts and not member.issym() and not member.islnk()
    tar.extractall(source)
commands = [('configure', ['cmake', '-S', str(source/'tests/collectives/memory'), '-B', str(root/'build'),
    '-DTILEXR_MEMORY_BUILD_SIMULATOR=ON', '-DASCEND_HOME_PATH=/opt/sim/Ascend/cann-9.1.0']),
    ('compile', ['cmake', '--build', str(root/'build'), '--target', 'tilexr_alltoall_direct_two_group',
                 'tilexr_alltoall_direct_two_group_clock', 'dump_alltoall_mem_layout', '-j4'])]
for stage, command in commands:
    log = root/(stage+'.log')
    with log.open('wb') as stream:
        p = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=240)
    if p.returncode:
        errors = [r[:300] for r in log.read_text(errors='replace').splitlines() if 'error:' in r][:6]
        print(json.dumps(dict(status=stage+'_failed', root=str(root), errors=errors)))
        raise SystemExit(1)
layout = root/'layout.json'
layout.write_bytes(subprocess.check_output([str(root/'build/dump_alltoall_mem_layout')]))
assert json.loads(layout.read_text())['kernel_args_size'] == 96
env['source_root'] = str(source)
(root/'environment-report.json').write_text(json.dumps(env, indent=2))
base = json.loads((old/'build-group.json').read_text())
base.update(runtime_commit=revision, source_files={str(p.relative_to(source)): digest(p)
    for p in source.rglob('*') if p.is_file()})
base['layout'].update(path=str(layout), sha256=digest(layout))
rows = []
for name, obj, variant in [('control', 'tilexr_alltoall_direct_two_group.o', 'direct-two-group'),
    ('clock', 'tilexr_alltoall_direct_two_group_clock.o', 'direct-two-group-clock')]:
    binary = root/'build'/obj
    report = json.loads(json.dumps(base))
    report['kernel'].update(path=str(binary), sha256=digest(binary), variant=variant,
                            entry='TileXRAllToAllMemSim', block_dim=64)
    (root/('build-'+name+'.json')).write_text(json.dumps(report, indent=2))
    rows.append(dict(variant=name, kernel_sha256=digest(binary), kernel_bytes=binary.stat().st_size))
summary = dict(status='compiled', runtime_commit=revision, root=str(root), records=rows,
    environment_assets=len(env['files']), source_hashes_verified=True, simulation_launched=False,
    control_matches_prior=rows[0]['kernel_sha256']=='485359c0ef685c59e73ca266546f380e8ab2dcb70c00af114cd98be911ce453a')
(root/'build-summary.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary))
