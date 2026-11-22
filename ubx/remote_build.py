"""中文：仅在任务独立目录解包可信来源快照并构建；保留编译日志和文件哈希。
English: Unpack an origin-pinned snapshot in the task directory and retain build logs and artifact hashes.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile

root = Path(SPEC['root'])
root.mkdir(exist_ok=False)
raw = base64.b64decode(SPEC['source_b64'])
assert hashlib.sha256(raw).hexdigest() == SPEC['source_sha256']
(root / 'source.tar.gz').write_bytes(raw)
source = root / 'source'
source.mkdir()
with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
    for member in archive.getmembers():
        assert not member.issym() and not member.islnk()
        assert (source / member.name).resolve().is_relative_to(source.resolve())
    archive.extractall(source)
environment = subprocess.run(['bash', '-c', '. /usr/local/Ascend/cann-9.2.T101/set_env.sh >/dev/null 2>&1; env -0'], capture_output=True, check=True, timeout=20)
env = dict(x.decode().split('=', 1) for x in environment.stdout.split(b'\0') if b'=' in x)
env['ASCEND_HOME_PATH'] = '/usr/local/Ascend/cann-9.2.T101'
env['ASCEND_DRIVER_PATH'] = '/usr/local/Ascend/driver'
env['LIBRARY_PATH'] = env['ASCEND_HOME_PATH'] + '/x86_64-linux/devlib:' + env.get('LIBRARY_PATH', '')
steps = []
def run(label, argv, timeout=900):
    with (root / (label + '.log')).open('wb') as log:
        try:
            result = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=timeout)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    steps.append(dict(step=label, exit=code, log=str(root / (label + '.log'))))
    (root / 'steps.json').write_text(json.dumps(steps, indent=2))
    return code == 0

build = root / 'build-TRACE'
install = root / 'install-TRACE'
host = root / 'host-TRACE'
ok = run('configure', ['cmake', '-S', str(source), '-B', str(build), '-DTILEXR_BUILD_EP=ON', '-DTILEXR_BUILD_UNITEST=OFF', '-DBUILD_TESTING=OFF', '-DTILEXR_BUILD_TESTS=OFF', '-DTILEXR_EP_UBX_COMBINE_PROFILING=ON', '-DTILEXR_EP_UBX_COMBINE_TRACE=ON', '-DTILEXR_DIAGNOSTICS_SOC_TYPE=Ascend950', '-DCMAKE_INSTALL_LIBDIR=lib', '-DCMAKE_INSTALL_PREFIX=' + str(install)], 120)
if ok:
    ok = run('build', ['cmake', '--build', str(build), '--target', 'tilexr-ep', 'tilexr-diagnostics', '-j4'], 1500)
if ok:
    ok = run('install', ['cmake', '--install', str(build)], 120)
if ok:
    ok = run('host-configure', ['cmake', '-S', str(source / 'tests/ep/ubx_combine_push'), '-B', str(host), '-DTILEXR_INSTALL_PREFIX=' + str(install)], 120)
if ok:
    ok = run('host-build', ['cmake', '--build', str(host), '-j4'], 180)
artifacts = {}
if ok:
    for path in [host / 'combine_push', *sorted((install / 'lib').rglob('*.so')), *sorted((install / 'lib/tilexr/kernels').glob('*.o')), source / 'src/ep/profiling/ubx_combine_push.mapping.json']:
        artifacts[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
answer = dict(status='BUILD_READY' if ok else 'BUILD_FAILED', hostname=socket.gethostname(), revision=SPEC['revision'], source_sha256=SPEC['source_sha256'], steps=steps, artifacts=artifacts, hardware_started=False)
(root / 'build-result.json').write_text(json.dumps(answer, indent=2))
print(json.dumps(answer, separators=(',', ':')))
raise SystemExit(0 if ok else 1)
