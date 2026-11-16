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

EXP = Path('/root/tilelang-107-f9-p2-b520293d-20261116-run01')
RUN = EXP / 'p2-capture-t256-20261116-01'
TOKENS = 256
EXPECTED_CORRECTNESS = {'schema': 'tilelang_f9_p2_107_correctness.v1', 'task_id': 'tl-a5-107-f9-p2-correctness-20261116-01', 'revision': 'b520293d5f014fcbd4897a334d50ebf9ca9c601b', 'phase': 'f9-correctness', 'target': '141.61.73.107', 'devices': [0, 1], 'cores': [64, 64], 'status': 'PASS', 'npu_launch': True, 'official_hccl_launch': False, 'clock': 'NOT_VERIFIED', 'groups': [{'group': 'g001', 'index': 1, 'phase': 'f9-correctness', 'mode': 'correctness', 'variant': 'f9', 'case': 'p2-e4-t256-balanced', 'tokens': 256, 'round': 1, 'hccl_base_port': 24000, 'comm_port': 29000, 'status': 'PASS', 'summary_sha256': 'ea3b3c570aead80453e8fd1827bb94b38186a826ad2861d5e4ae03907c5204b6', 'analysis_sha256': '67595fae2136d3a1c784174ea418c930756642223e76ac2df3275fac36963c9f'}, {'group': 'g002', 'index': 2, 'phase': 'f9-correctness', 'mode': 'correctness', 'variant': 'f9', 'case': 'p2-e4-t256-balanced', 'tokens': 256, 'round': 2, 'hccl_base_port': 24064, 'comm_port': 29001, 'status': 'PASS', 'summary_sha256': '9462653bcdc129343db701019c16f390e404c6e7c8960dc5847f9710db4a2651', 'analysis_sha256': '0b9ce6ce1380b8b1122b97bdc2e539ab9d0cca63a0dfe39fee472f44adfae160'}, {'group': 'g003', 'index': 3, 'phase': 'f9-correctness', 'mode': 'correctness', 'variant': 'f9', 'case': 'p2-e4-t256-balanced', 'tokens': 256, 'round': 3, 'hccl_base_port': 24128, 'comm_port': 29002, 'status': 'PASS', 'summary_sha256': '0a4ed72a9ff2105288a57332bbd6f341800b5bd7d37f29bef3d82213ede2c007', 'analysis_sha256': '87cc2600bdab3bc4942143bbc8b53765fbb68b33e929fc5cee5e5140d8fcd1fe'}], 'artifact_manifest_sha256': 'f9c77fa3454a00c22e41fce57998e100e243c5d15008bcd41adcfb1639332272', 'provenance_sha256': 'd01cd022ae904cd194836875dfa3a618918d5f55b67a1b47c82c6850ce2a5975', 'cases': 1, 'fresh_rank_groups': 3, 'rank_receipts': 6, 'rank_dispatch_iterations': 126, 'allreduce_values_checked': 132, 'profiling': False, 'warmup_per_group': 20, 'measured_per_group': 1, 'namespace_exit': 0, 'timed_out': False, 'own_residual_children': [], 'cleanup_actions': [], 'original_configuration_preserved': True, 'qualification_reused': True, 'qualification_receipt_sha256': 'e22807e4b530ded459533c840eb9e77722204776f5e99f2994d5a0640fe1b609', 'qualification_environment_key': 'e4db0e0177a56f1daa97c54c6e7a296067a74315b33a7c06b96a65d0a4b67836', 'run_dir': '/root/tilelang-107-f9-p2-b520293d-20261116-run01/p2-correctness-20261116-01', 'scope': '107 physical0-1 P2/E4/T256 private layer0 UB Config4; F9 b520 SUM2 functional correctness only'}
REV = 'b520293d5f014fcbd4897a334d50ebf9ca9c601b'
TASK = 'tl-a5-107-f9-p2-capture-t256-20261116-01'
ARTIFACT_SHA = 'f9c77fa3454a00c22e41fce57998e100e243c5d15008bcd41adcfb1639332272'
PROVENANCE_SHA = 'd01cd022ae904cd194836875dfa3a618918d5f55b67a1b47c82c6850ce2a5975'
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
    assert build['status'] == 'PASS' and build['revision'] == REV and build['cores'] == [64, 64]
    assert build['world_size'] == 2 and build['experts'] == 4 and build['tokens_per_rank'] == 256
    assert build['p4_compiled_artifacts_reused'] is False
    assert build['core_cxx11_abi'] == 0 and build['runtime_validation'] == 'NOT_RUN'
    assert build['core_library_sha256'] == '382f9849dfc5f5dc9478a8e33c0179ad309329dc75c4bcb0e46782b412855b56'
    assert sha(QUAL) == QUAL_SHA
    qualification = json.loads(QUAL.read_text())
    assert qualification['status'] == 'PASS' and not qualification['own_residual_children']
    assert qualification['identity']['devices'] == [0, 1, 2, 3]
    for target, (source, digest) in PRIVATE.items():
        assert sha(PRE / source) == digest
    # Bind capture permission to the exact three completed P2 receipts; never replay them.
    prior_dir = EXP / 'p2-correctness-20261116-01'
    previous = json.loads((prior_dir / 'summary.json').read_text())
    assert previous == EXPECTED_CORRECTNESS, 'P2 correctness evidence differs from received PASS'
    for group in previous['groups']:
        directory = prior_dir / 'groups' / group['group']
        assert sha(directory / 'group-summary.json') == group['summary_sha256']
        assert sha(directory / 'analysis.json') == group['analysis_sha256']
    prior_sha = sha(prior_dir / 'summary.json')
    assert not RUN.exists(), 'Saved run exists; inspect without replay'
    RUN.mkdir(mode=0o750)
    save(RUN / 'task.json', {'task_id': TASK, 'revision': REV, 'state': 'PREPARING', 'npu_launch': False})
    for name, resource in RESOURCES.items():
        assert name in ('runtime_group.py', 'capture_controller.py', 'check_npu_status.py', 'group-plan.json')
        raw = base64.b64decode(resource['base64'], validate=True)
        assert hashlib.sha256(raw).hexdigest() == resource['sha256']
        (RUN / name).write_bytes(raw)
    save(RUN / 'env_list.json', {'version': 1, 'environments':
         [{'name': 'a5-107', 'ssh': 'local', 'devices': [0, 1]}]})
    config = {'task_id': TASK, 'revision': REV, 'phase': 'capture-t256', 'mode': 'capture', 'tokens': TOKENS,
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
            str(RUN / 'capture_controller.py'), original_ns, json.dumps(spec)]
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
        deadline, timed, count = time.monotonic() + 3500, False, -1
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
        'schema': 'tilelang_f9_p2_107_capture.v1', 'task_id': TASK, 'revision': REV,
        'status': 'FAIL_OR_UNVERIFIED', 'npu_launch': None, 'groups': []}
    preserved = all(sha(name) == digest for name, digest in SYSTEM.items()) and original_ns == os.readlink('/proc/self/ns/mnt')
    summary.update(namespace_exit=code, timed_out=timed, own_residual_children=residual,
                   cleanup_actions=cleanup, original_configuration_preserved=preserved,
                   qualification_reused=True, correctness_summary_sha256=prior_sha,
                   correctness_task_id=previous['task_id'], qualification_receipt_sha256=QUAL_SHA,
                   qualification_environment_key=qualification['environment_key'], run_dir=str(RUN),
                   scope='107 physical0-1 P2/E4/T256 private layer0 UB Config4; F9 b520 SUM2 ten fresh profile1/msprof 20+1 groups; raw ticks NOT_VERIFIED')
    if code != 0 or timed or residual or cleanup or not preserved:
        summary['status'] = 'FAIL_OR_UNVERIFIED'
    save(RUN / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=True, separators=(',', ':')))
    return 0 if summary['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
