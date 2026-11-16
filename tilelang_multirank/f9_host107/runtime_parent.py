"""107 private L0 namespace parent. RESOURCES is injected by the pinned Bridge launcher."""
from pathlib import Path
import base64
import hashlib
import json
import os
import shlex
import signal
import subprocess
import time

EXP = Path('/root/tilelang-107-f9-host107-b520293d-20261116-run01')
RUN = EXP / 'f9-correctness-20261116-01'
REV = 'b520293d5f014fcbd4897a334d50ebf9ca9c601b'
TASK = 'tl-a5-107-f9-host107-correctness-20261116-01'
ARTIFACT_SHA = '2e30632f8e71376742c47ca4b1d7bacebee5d8aaea80ae8e78e0324fbddfcfe7'
PROVENANCE_SHA = 'f56caa6f4e589110eefc3a01eeec8e4d37ca4899a879e01f7a790670f155add1'
SYSTEM = {
    '/etc/hccl_rootinfo.json': '56a0d1eb4c38422fbbdd946b34036ed6496fe36c1be95045d0519f35d69ad523',
    '/usr/local/Ascend/driver/topo/950/atlas_850_1.json': '65e257641adea221a576bab32710f759a59df80b50bc8ef613b38e10c6164db9',
}
PRIVATE = {
    '/etc/hccl_rootinfo.json': ('rootinfo-layer0.json', '61ccdd60085f52a721864c2077eed60f9e3e9a080f23b6a919dd901dfebd91ea'),
    '/usr/local/Ascend/driver/topo/950/atlas_850_1.json': ('topology-layer0.json', 'd54ecef1dea84ea6c8106d502cc173afc43a9bab6b21d117e4a1b7d9b152e09d'),
}
PRE = Path('/root/tilelang-107-p4-peer-view-preflight-20261116-01')
QUAL = Path('/root/tilelang-hccl-qualifications/693ec683dcc80807f1ccc0c0356a6e032008fd71e1a9a1538c435eaa1a153dc6/supplements/layer0-peer-private-view-single/receipt.json')
QUAL_SHA = 'e22807e4b530ded459533c840eb9e77722204776f5e99f2994d5a0640fe1b609'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n')
    temporary.replace(path)


def state(pid):
    try:
        fields = Path('/proc/' + str(pid) + '/stat').read_text().split(') ', 1)[1].split()
        return {'pid': pid, 'start_ticks': fields[19], 'pgid': int(fields[2]), 'state': fields[0]}
    except FileNotFoundError:
        return None


def live(record):
    current = state(record['pid'])
    return current is not None and current['start_ticks'] == record['start_ticks'] and current['state'] != 'Z'


def main():
    assert os.geteuid() == 0 and sha('/etc/machine-id') == '78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'
    assert all(sha(name) == digest for name, digest in SYSTEM.items()), 'Global configuration changed'
    assert sha(EXP / 'artifact-hashes.json') == ARTIFACT_SHA and sha(EXP / 'provenance.json') == PROVENANCE_SHA
    build = json.loads((EXP / 'build-summary.json').read_text())
    assert build['status'] == 'PASS' and build['revision'] == REV and build['cores'] == [64] * 4
    assert build['core_cxx11_abi'] == 0 and build['runtime_validation'] == 'NOT_RUN'
    assert build['core_library_sha256'] == '1c559dc6c64cf36b74a1c02d80e9b937bbd9f07a4f87b29c8175cdcc8e25b5e1'
    assert sha(QUAL) == QUAL_SHA
    qualification = json.loads(QUAL.read_text())
    assert qualification['status'] == 'PASS' and not qualification['own_residual_children']
    assert qualification['identity']['devices'] == [0, 1, 2, 3]
    for target, (source, digest) in PRIVATE.items():
        assert sha(PRE / source) == digest
    assert not RUN.exists(), 'Saved run exists; inspect without replay'
    RUN.mkdir(mode=0o750)
    save(RUN / 'task.json', {'task_id': TASK, 'revision': REV, 'state': 'PREPARING', 'npu_launch': False})
    for name, resource in RESOURCES.items():
        assert name in ('runtime_group.py', 'runtime_controller.py', 'check_npu_status.py', 'group-plan.json')
        raw = base64.b64decode(resource['base64'], validate=True)
        assert hashlib.sha256(raw).hexdigest() == resource['sha256']
        (RUN / name).write_bytes(raw)
    save(RUN / 'env_list.json', {'version': 1, 'environments':
         [{'name': 'a5-107', 'ssh': 'local', 'devices': [0, 1, 2, 3]}]})
    config = {'task_id': TASK, 'revision': REV, 'phase': 'f9-correctness', 'mode': 'correctness',
              'artifact_manifest_sha256': ARTIFACT_SHA, 'provenance_sha256': PROVENANCE_SHA,
              'checker_sha256': sha(RUN / 'check_npu_status.py'),
              'group_worker_sha256': sha(RUN / 'runtime_group.py'),
              'group_plan_sha256': sha(RUN / 'group-plan.json')}
    save(RUN / 'config.json', config)
    namespace = '''from pathlib import Path
import hashlib,json,os,subprocess,sys
assert os.readlink('/proc/self/ns/mnt') != sys.argv[2]
spec=json.loads(sys.argv[3])
for target, source, digest in spec:
    subprocess.run(['mount','--bind',source,target],check=True,capture_output=True,timeout=10)
    subprocess.run(['mount','-o','remount,bind,ro',target],check=True,capture_output=True,timeout=10)
    assert hashlib.sha256(Path(target).read_bytes()).hexdigest()==digest
os.execv('/usr/bin/python3',['/usr/bin/python3',sys.argv[1],str(Path(sys.argv[1]).parent/'config.json')])
'''
    (RUN / 'namespace.py').write_text(namespace)
    original_ns = os.readlink('/proc/self/ns/mnt')
    spec = [[target, str(PRE / source), digest] for target, (source, digest) in PRIVATE.items()]
    argv = ['unshare', '--mount', '--propagation', 'private', '/usr/bin/python3', str(RUN / 'namespace.py'),
            str(RUN / 'runtime_controller.py'), original_ns, json.dumps(spec)]
    assert not any(os.environ.get(k) for k in ('LD_PRELOAD', 'ASCEND_VISIBLE_DEVICES', 'ASCEND_RT_VISIBLE_DEVICES', 'TILEXR_UDMA_ATTACH_EXISTING_RA'))
    prefix = ('source /usr/local/Ascend/cann-9.1.0/set_env.sh >/dev/null 2>&1 && '
              'export LD_LIBRARY_PATH=' + shlex.quote(str(EXP / 'core-install/lib64')) + ':${LD_LIBRARY_PATH:-} && '
              'export TILEXR_UDMA_ATTACH_EXISTING_RA=1 HCCL_CONNECT_TIMEOUT=120 HCCL_EXEC_TIMEOUT=180 && exec ')
    seen = {}

    def collect(pid):
        current = state(pid)
        if current is None:
            return
        seen.setdefault(pid, current)
        try:
            children = Path('/proc/' + str(pid) + '/task/' + str(pid) + '/children').read_text().split()
        except FileNotFoundError:
            children = []
        for child in children:
            collect(int(child))

    with (RUN / 'namespace.stdout.log').open('xb') as out, (RUN / 'namespace.stderr.log').open('xb') as err:
        p = subprocess.Popen(['bash', '-c', prefix + shlex.join(argv)], stdin=subprocess.DEVNULL,
                             stdout=out, stderr=err, start_new_session=True)
        save(RUN / 'launcher.json', {'pid': p.pid, 'identity': state(p.pid), 'argv': argv})
        deadline, timed, count = time.monotonic() + 900, False, -1
        while p.poll() is None:
            collect(p.pid)
            if len(seen) != count:
                save(RUN / 'owned-processes.json', list(seen.values()))
                count = len(seen)
            if time.monotonic() >= deadline:
                timed = True
                break
            time.sleep(.1)
        cleanup = []
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for record in sorted(seen.values(), key=lambda row: row['pid'], reverse=True):
                if not live(record):
                    continue
                cleanup.append({'pid': record['pid'], 'signal': int(sig)})
                try:
                    if record['pgid'] == record['pid']:
                        os.killpg(record['pid'], sig)
                    else:
                        os.kill(record['pid'], sig)
                except ProcessLookupError:
                    pass
            end = time.monotonic() + 3
            while time.monotonic() < end and any(live(row) for row in seen.values()):
                time.sleep(.1)
        code = p.wait(timeout=5)
    residual = [record for record in seen.values() if live(record)]
    path = RUN / 'worker-summary.json'
    summary = json.loads(path.read_text()) if path.exists() else {
        'schema': 'tilelang_107_f9_host107_correctness.v1', 'task_id': TASK, 'revision': REV,
        'status': 'FAIL_OR_UNVERIFIED', 'npu_launch': None, 'groups': []}
    preserved = all(sha(name) == digest for name, digest in SYSTEM.items()) and original_ns == os.readlink('/proc/self/ns/mnt')
    summary.update(namespace_exit=code, timed_out=timed, own_residual_children=residual,
                   cleanup_actions=cleanup, original_configuration_preserved=preserved,
                   qualification_reused=True, qualification_receipt_sha256=QUAL_SHA,
                   qualification_environment_key=qualification['environment_key'], run_dir=str(RUN),
                   scope='107 physical0-3 private layer0 UB Config4; F9+validated Host functional correctness only')
    if code != 0 or timed or residual or cleanup or not preserved:
        summary['status'] = 'FAIL_OR_UNVERIFIED'
    save(RUN / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=True, separators=(',', ':')))
    return 0 if summary['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
