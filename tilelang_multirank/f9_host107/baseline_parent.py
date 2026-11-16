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
RUN = EXP / 'baseline-correctness-20261116-01'
REV = 'b520293d5f014fcbd4897a334d50ebf9ca9c601b'
EXPECTED_F9_JSON = '{"schema":"tilelang_107_f9_host107_correctness.v1","task_id":"tl-a5-107-f9-host107-correctness-20261116-01","revision":"b520293d5f014fcbd4897a334d50ebf9ca9c601b","phase":"f9-correctness","target":"141.61.73.107","devices":[0,1,2,3],"cores":[64,64,64,64],"status":"PASS","npu_launch":true,"official_hccl_launch":false,"clock":"NOT_VERIFIED","groups":[{"group":"g000","index":0,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-balanced","tokens":16,"round":1,"mode":"correctness","hccl_base_port":61024,"comm_port":61064,"status":"PASS","summary_sha256":"be8107eae3a73e409c44df3fde75ced580415139ae885ff894da97d5138b8194","analysis_sha256":"0337df257de5a79c43f3674db5fd274df59994b032bd167fb46c70431cea8bf5"},{"group":"g001","index":1,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-balanced","tokens":16,"round":2,"mode":"correctness","hccl_base_port":61072,"comm_port":61112,"status":"PASS","summary_sha256":"515c30fdd829bd2a46a903d3e1b3c4efa03493bff8fabb61a640b6ea226ace7e","analysis_sha256":"33c5fb8b8bf1ae4c8b340e70077adecc8324cdbda55b674e4e93b01c99d0af67"},{"group":"g002","index":2,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-balanced","tokens":16,"round":3,"mode":"correctness","hccl_base_port":61120,"comm_port":61160,"status":"PASS","summary_sha256":"68f71e39fb418be3f201b312580a1b8dbb015103fa67e68d369e5a3853550906","analysis_sha256":"5e0c4a67efdf5117cbab2b22990e71dda7d33600bf60a6f2c86b028bb9fac30e"},{"group":"g003","index":3,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t64-balanced","tokens":64,"round":1,"mode":"correctness","hccl_base_port":61168,"comm_port":61208,"status":"PASS","summary_sha256":"cedc56d77426afc54b8f0e031cc1f1ee6d11a84c31f98a7a8438d1437a1fbd73","analysis_sha256":"596a79797d41e7e00297d5d374649abf9addef48cc24702d6a662e75af67f339"},{"group":"g004","index":4,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t64-balanced","tokens":64,"round":2,"mode":"correctness","hccl_base_port":61216,"comm_port":61256,"status":"PASS","summary_sha256":"ecff14840287c466853be33eaa9cf6ddb4147b8d2ecbe039af719d9230cdf138","analysis_sha256":"5ec88434714251668d5ad9788e21eb0ab83a04bb1397325e5dbd8f23a4a0ecd5"},{"group":"g005","index":5,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t64-balanced","tokens":64,"round":3,"mode":"correctness","hccl_base_port":61264,"comm_port":61304,"status":"PASS","summary_sha256":"9f56cb41edd9cc230e3ced69447c9364ce1249a01ef7d6c8da3fc958b44043c0","analysis_sha256":"6a2e24ffe695669fcb40f1750e034f74d95917de2c93111119b4cda5a3c4eace"},{"group":"g006","index":6,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t256-balanced","tokens":256,"round":1,"mode":"correctness","hccl_base_port":61312,"comm_port":61352,"status":"PASS","summary_sha256":"0204f3a61d596dfa370296881162892eee5603f160b2afa4087623b8995f415c","analysis_sha256":"a0e70e874035cd4a6399136c4dfd7efd088e88807fe4a5fd2f74d82a90d9c418"},{"group":"g007","index":7,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t256-balanced","tokens":256,"round":2,"mode":"correctness","hccl_base_port":61360,"comm_port":61400,"status":"PASS","summary_sha256":"626c39954918d8fb2cddaf92dbe95b77302cf5a65f979bb4885fdaaaf06af861","analysis_sha256":"bb1c9a09c1fd2da75260c2dcb51a47ab6cd70cd6b0c99786e73b169c1ad07dc2"},{"group":"g008","index":8,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t256-balanced","tokens":256,"round":3,"mode":"correctness","hccl_base_port":61408,"comm_port":61448,"status":"PASS","summary_sha256":"36d6b13e67f7d7a1e21a250d6328ee8b342ad1947833014d72d14918f4863cda","analysis_sha256":"405517ef00462a2fae6d52e33e1e6cd5b08a46cb689a91f91ae25a2f5fc6fa5a"},{"group":"g009","index":9,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-local_only","tokens":16,"round":1,"mode":"correctness","hccl_base_port":61456,"comm_port":61496,"status":"PASS","summary_sha256":"8bd4d5ca57d8b4f062371795515acdd48dd1eed4652c248b59217ea8e8813ddd","analysis_sha256":"841c09ed4ad64b5b4558ba3c289fc18ae5fb70c5c49a9c691027d3950b7ee27f"},{"group":"g010","index":10,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-local_only","tokens":16,"round":2,"mode":"correctness","hccl_base_port":61504,"comm_port":61544,"status":"PASS","summary_sha256":"8eabac410286116dca81c24aae66f0a961f00e0a8beb79c739a60a7183dfdfc3","analysis_sha256":"9767b94e92ed9fc917b3c21876d98c8777fc819845f34d5c76ef852640c4c650"},{"group":"g011","index":11,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-local_only","tokens":16,"round":3,"mode":"correctness","hccl_base_port":61552,"comm_port":61592,"status":"PASS","summary_sha256":"b261939cefc714277f19116f5666b74a5f26d1f21af3fb614424f7afa1fb77cd","analysis_sha256":"cffc1245193bffc657a2d03630c8e761d5636764adc3337fb5d5251020874a0b"},{"group":"g012","index":12,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-ring_peer","tokens":16,"round":1,"mode":"correctness","hccl_base_port":61600,"comm_port":61640,"status":"PASS","summary_sha256":"432d3610dba65c74b8dd73899e6ab5245f309ac1db049a4108b67331f9417491","analysis_sha256":"3cfae00f9e66565805d3d88d60ce739712084648dd6204ef1a42174e0f3b8cd4"},{"group":"g013","index":13,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-ring_peer","tokens":16,"round":2,"mode":"correctness","hccl_base_port":61648,"comm_port":61688,"status":"PASS","summary_sha256":"385f1066dbd863eaefa4f5dae518806def83f381becddf8f1e5fac72a5333c51","analysis_sha256":"139aa957d4bf938ce40e53e0b1f8dd025b1fcb7a543696867bb3b1d11f059763"},{"group":"g014","index":14,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-ring_peer","tokens":16,"round":3,"mode":"correctness","hccl_base_port":61696,"comm_port":61736,"status":"PASS","summary_sha256":"cafed8b6249c52e438d6c1bf0cd2a1666b23c6ffa1156f9022662e18025fa531","analysis_sha256":"8b4f188bcca82ddbd0385229a1a70f8da98c58536ded1a85b2da738bbbeeb117"},{"group":"g015","index":15,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-hotspot_last","tokens":16,"round":1,"mode":"correctness","hccl_base_port":61744,"comm_port":61784,"status":"PASS","summary_sha256":"be67ee39a34866df0d8367b280a1476c33e42baceec0adca2ab6913ee48b0569","analysis_sha256":"05a7e01aa3bf527e9c9d883c5252fe0512bda5699a9ddef1384a45b402f03272"},{"group":"g016","index":16,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-hotspot_last","tokens":16,"round":2,"mode":"correctness","hccl_base_port":61792,"comm_port":61832,"status":"PASS","summary_sha256":"aee84e06482c00c4e5ca3725b5f3ccf3f335b65174becf24ba633becf423ec42","analysis_sha256":"34a5e7568c7cb362a39a2450ddb2b1b8561eb381e12a0714ea638ce6eb647e78"},{"group":"g017","index":17,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-hotspot_last","tokens":16,"round":3,"mode":"correctness","hccl_base_port":61840,"comm_port":61880,"status":"PASS","summary_sha256":"070c73c7f3b1e9b983fe993051336ccba1164b91c443ea240821592bb5c6b646","analysis_sha256":"c514b6d7273405000e35dfdb372b3432c7a76745ed8aa696e3c583aa2c927488"},{"group":"g018","index":18,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-rotating_skew","tokens":16,"round":1,"mode":"correctness","hccl_base_port":61888,"comm_port":61928,"status":"PASS","summary_sha256":"ae96929a932da27ae528a94c10f2795f6bf56ee303c35a95b3dd0417f850eabf","analysis_sha256":"da827ba9e61373e5f327532fbc3c90227baab50cb5cb399afe49fbb94b39f8b9"},{"group":"g019","index":19,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-rotating_skew","tokens":16,"round":2,"mode":"correctness","hccl_base_port":61936,"comm_port":61976,"status":"PASS","summary_sha256":"1d804ab405fa8e4cde6341495bd2d6748f7264f6b9c82b5096f9cc3d7c321bda","analysis_sha256":"925ba8b9fbfb43c7862a2deb5706e3d8947e53e15030d0be961b56e906b53d87"},{"group":"g020","index":20,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t16-rotating_skew","tokens":16,"round":3,"mode":"correctness","hccl_base_port":61984,"comm_port":62024,"status":"PASS","summary_sha256":"4117b78c590a6c85ad2a2d87f23b40da0073c13009db22ec2aae1da5ee81a2a4","analysis_sha256":"ba15c94bf32720835a7574cac40f03ea3a5c8b329a0e1b138a659ad2557091dd"},{"group":"g021","index":21,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t4-sparse","tokens":4,"round":1,"mode":"correctness","hccl_base_port":62032,"comm_port":62072,"status":"PASS","summary_sha256":"5a9507a70f75a706a468b65207b4b977760c20e079c6ac258d38899326c583f3","analysis_sha256":"b8b1c2889721e2bd2b5375dc72f0c8c760f88e1f93d2407b9b930234a8c56c78"},{"group":"g022","index":22,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t4-sparse","tokens":4,"round":2,"mode":"correctness","hccl_base_port":62080,"comm_port":62120,"status":"PASS","summary_sha256":"b31677ecaa8353ca42d3821e7d03e6ae241abce77261814a01cef2a6e654fa8a","analysis_sha256":"7b598fd3a123034c40aa3cf9bb2eb7a4a6e5cb4a940aed5c8aeaa80a0abdf164"},{"group":"g023","index":23,"phase":"f9-correctness","variant":"f9","case":"p4-e8-t4-sparse","tokens":4,"round":3,"mode":"correctness","hccl_base_port":62128,"comm_port":62168,"status":"PASS","summary_sha256":"8ebf68149fe9253976729581a708a77a98d90b0c01ce35f7dd518cf1e42257fa","analysis_sha256":"2316f598b306fb1616a1e219ddef5c1eb8f6475e4f6ed31098ee1569d990c357"}],"artifact_manifest_sha256":"2e30632f8e71376742c47ca4b1d7bacebee5d8aaea80ae8e78e0324fbddfcfe7","provenance_sha256":"f56caa6f4e589110eefc3a01eeec8e4d37ca4899a879e01f7a790670f155add1","cases":8,"fresh_rank_groups":24,"rank_receipts":96,"rank_dispatch_iterations":2016,"allreduce_values_checked":2112,"profiling":false,"warmup_per_group":20,"measured_per_group":1,"namespace_exit":0,"timed_out":false,"own_residual_children":[],"cleanup_actions":[],"original_configuration_preserved":true,"qualification_reused":true,"qualification_receipt_sha256":"e22807e4b530ded459533c840eb9e77722204776f5e99f2994d5a0640fe1b609","qualification_environment_key":"e4db0e0177a56f1daa97c54c6e7a296067a74315b33a7c06b96a65d0a4b67836","run_dir":"/root/tilelang-107-f9-host107-b520293d-20261116-run01/f9-correctness-20261116-01"}'
TASK = 'tl-a5-107-f9-host107-baseline-correctness-20261116-01'
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
    # Read completed F9 evidence; never launch or replay its groups.
    prior_dir = EXP / 'f9-correctness-20261116-01'
    expected_prior = json.loads(EXPECTED_F9_JSON)
    prior = json.loads((prior_dir / 'summary.json').read_text())
    assert all(prior.get(k) == value for k, value in expected_prior.items()), 'F9 completed result differs from received evidence'
    for group in expected_prior['groups']:
        directory = prior_dir / 'groups' / group['group']
        assert sha(directory / 'group-summary.json') == group['summary_sha256']
        assert sha(directory / 'analysis.json') == group['analysis_sha256']
    prior_sha = sha(prior_dir / 'summary.json')
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
    config = {'task_id': TASK, 'revision': REV, 'phase': 'baseline-correctness', 'mode': 'correctness',
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
        deadline, timed, count = time.monotonic() + 660, False, -1
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
        'schema': 'tilelang_107_f9_host107_baseline_correctness.v1', 'task_id': TASK, 'revision': REV,
        'status': 'FAIL_OR_UNVERIFIED', 'npu_launch': None, 'groups': []}
    preserved = all(sha(name) == digest for name, digest in SYSTEM.items()) and original_ns == os.readlink('/proc/self/ns/mnt')
    summary.update(namespace_exit=code, timed_out=timed, own_residual_children=residual,
                   cleanup_actions=cleanup, original_configuration_preserved=preserved,
                   qualification_reused=True, f9_correctness_summary_sha256=prior_sha,
                   f9_correctness_task_id=expected_prior['task_id'], qualification_receipt_sha256=QUAL_SHA,
                   qualification_environment_key=qualification['environment_key'], run_dir=str(RUN),
                   scope='107 physical0-3 private layer0 UB Config4; same-Host baseline balanced T16/T64/T256 functional correctness only')
    if code != 0 or timed or residual or cleanup or not preserved:
        summary['status'] = 'FAIL_OR_UNVERIFIED'
    save(RUN / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=True, separators=(',', ':')))
    return 0 if summary['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
