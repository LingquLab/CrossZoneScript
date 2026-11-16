"""One fresh 107 four-rank group. Called only by the private-namespace controller."""
from pathlib import Path
from types import SimpleNamespace
import concurrent.futures
import datetime
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import zipfile

EXP = Path('/root/tilelang-107-f9-host107-b520293d-20261116-run01')
OLD = Path('/root/tilelang-107-multirank-e28d87de-20261116-run01')
CANN = Path('/usr/local/Ascend/cann-9.1.0')
REV = 'b520293d5f014fcbd4897a334d50ebf9ca9c601b'
QUAL = Path('/root/tilelang-hccl-qualifications/693ec683dcc80807f1ccc0c0356a6e032008fd71e1a9a1538c435eaa1a153dc6/supplements/layer0-peer-private-view-single/receipt.json')
QUAL_SHA = 'e22807e4b530ded459533c840eb9e77722204776f5e99f2994d5a0640fe1b609'
PRIVATE = {
    '/etc/hccl_rootinfo.json': '61ccdd60085f52a721864c2077eed60f9e3e9a080f23b6a919dd901dfebd91ea',
    '/usr/local/Ascend/driver/topo/950/atlas_850_1.json': 'd54ecef1dea84ea6c8106d502cc173afc43a9bab6b21d117e4a1b7d9b152e09d',
}
LIBRARIES = {
    'libhccl.so': '25dc451dc22780a11c0dd58065a5f90fece8945986c756aa80831265ce261807',
    'libhccl_v2.so': '1b3a4d8a1d09e03f036ca4371c4304f4deb0ee374c2527affaac7623e81bdb04',
    'libhcomm.so': 'e1ebc2e879cc923d1e2b310706a15ebb7250bde6644780875584ce5af3cb530c',
}
CASES = {'p4-e8-t16-balanced': 16, 'p4-e8-t64-balanced': 64,
         'p4-e8-t256-balanced': 256, 'p4-e8-t16-local_only': 16,
         'p4-e8-t16-ring_peer': 16, 'p4-e8-t16-hotspot_last': 16,
         'p4-e8-t16-rotating_skew': 16, 'p4-e8-t4-sparse': 4}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n')
    temporary.replace(path)


def query(device, kind, directory, label):
    p = subprocess.run(['npu-smi', 'info', '-t', kind, '-i', str(device)],
                       capture_output=True, timeout=15)
    prefix = directory / (label + '-device' + str(device) + '-' + kind)
    prefix.with_suffix('.stdout.log').write_bytes(p.stdout)
    prefix.with_suffix('.stderr.log').write_bytes(p.stderr)
    good = (bool(re.search(rb'Health Status\s*:\s*OK\s*$', p.stdout, re.M))
            if kind == 'health' else b'No process in device.' in p.stdout)
    return {'device': device, 'exit_code': p.returncode,
            'ok' if kind == 'health' else 'idle': p.returncode == 0 and good}


def query_all(kind, directory, label):
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(lambda d: query(d, kind, directory, label), range(4)))


def port_gate(base, comm):
    ephemeral = [int(x) for x in Path('/proc/sys/net/ipv4/ip_local_port_range').read_text().split()]
    ports = list(range(base, base + 32)) + [comm]
    require(len(ports) == len(set(ports)) and all(1024 <= p <= 65535 for p in ports), 'Invalid port plan')
    require(all(not ephemeral[0] <= p <= ephemeral[1] for p in ports), 'Test port overlaps ephemeral range')
    for port in ports:
        for family, address in ((socket.AF_INET, '0.0.0.0'), (socket.AF_INET6, '::')):
            with socket.socket(family, socket.SOCK_STREAM) as s:
                if family == socket.AF_INET6:
                    s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                s.bind((address, port))
    return {'hccl_base_port': base, 'comm_port': comm, 'ports_checked': len(ports),
            'ipv4_ipv6_bind': True, 'ephemeral_range': ephemeral,
            'reservation': 'snapshot only; no global reservation or sysctl change'}


def integrity(config):
    require(sha('/etc/machine-id') == '78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0', 'Host identity changed')
    for name, expected in PRIVATE.items():
        require(sha(name) == expected, 'Private namespace configuration changed')
    require(sha(QUAL) == QUAL_SHA, 'Qualification receipt changed')
    for name, expected in LIBRARIES.items():
        require(sha(CANN / 'lib64' / name) == expected, 'Qualified library changed')
    require(sha(EXP / 'artifact-hashes.json') == config['artifact_manifest_sha256'], 'Build artifact manifest changed')
    require(sha(EXP / 'provenance.json') == config['provenance_sha256'], 'Build provenance changed')
    build = json.loads((EXP / 'build-summary.json').read_text())
    require(build['status'] == 'PASS' and build['revision'] == REV and build['cores'] == [64] * 4,
            'Independent A/B build not verified')
    provenance = json.loads((EXP / 'provenance.json').read_text())
    require(provenance['revision'] == REV and provenance['baseline_revision'] == '85e5dc214ab885d46be7c4182aa3d7b7dc910140'
            and provenance['baseline_host_overlay_revision'] == REV and provenance['core_cxx11_abi'] == 0,
            'A/B provenance mismatch')
    for name, expected in json.loads((EXP / 'artifact-hashes.json').read_text()).items():
        require(sha(EXP / name) == expected, 'Built artifact changed: ' + name)
    for variant in ('baseline', 'f9'):
        source = EXP / variant / 'TileXR'
        with zipfile.ZipFile(EXP / 'ab.zip') as z:
            manifest_raw = z.read(variant + '/source-manifest.json')
            require((EXP / variant / 'source-manifest.json').read_bytes() == manifest_raw,
                    'Source manifest changed')
            for name, expected in json.loads(manifest_raw).items():
                require(sha(source / name) == expected, 'Candidate source changed: ' + name)
            for name in z.namelist():
                if name.startswith(variant + '/generated/'):
                    require((EXP / name).read_bytes() == z.read(name), 'Generated export changed: ' + name)
        for name, expected in provenance['host_sha256'].items():
            require(sha(source / 'examples/tilelang_dispatch' / name) == expected, 'Host mismatch')
    require(sha(EXP / 'ab.zip') == '5a4011c99da6ffcca4c5ce743778f090aae4f4d3a2345f4b4c6191b653984ba9', 'A/B bundle changed')
    require(sha(EXP / 'fixture-hashes.json') == provenance['fixture_hashes_sha256'], 'Fixture manifest changed')
    for name, expected in json.loads((EXP / 'fixture-hashes.json').read_text()).items():
        require(sha(EXP / name) == expected, 'Fixed fixture changed: ' + name)
    # The module imports the parser from the retained TileXR tree, outside its 123-file overlay.
    require(sha(OLD / 'dependencies.zip') == provenance['dependency_zip_sha256'], 'Retained dependency ZIP changed')
    with zipfile.ZipFile(OLD / 'dependencies.zip') as z:
        manifest = json.loads(z.read('dependency-manifest.json'))['files']
        for variant in ('baseline', 'f9'):
            for name, expected in manifest.items():
                if name.startswith('TileXR/tools/profiling/python/'):
                    require(sha(EXP / variant / name) == expected, 'Profiling parser changed')
    return {'status': 'PASS', 'artifact_manifest_sha256': config['artifact_manifest_sha256'],
            'provenance_sha256': config['provenance_sha256'], 'qualification_receipt_sha256': QUAL_SHA}


def validate_native(output, fixture, generated, capture):
    from tilelang_dispatch.analyze_native import analyze
    analysis = analyze(output, fixture, generated)
    require(analysis['status'] == 'PASS' and analysis['world_size'] == 4
            and analysis['rounds'] == 1 and analysis['warmup'] == 20
            and analysis['capture_checked'] == capture, 'Full native analysis failed')
    batch = output / 'round1'
    rows = []
    for rank in range(4):
        root = batch / ('rank' + str(rank))
        log = (batch / ('rank' + str(rank) + '.log')).read_text(errors='replace')
        markers = ['TL950_HOST_LIFECYCLE HCCL_READY mode=4', 'TL950_HOST_LIFECYCLE TILEXR_READY',
                   'TL950_HOST_LIFECYCLE TILEXR_CLOSED', 'TL950_HOST_LIFECYCLE HCCL_CLOSED']
        positions = [log.index(m) for m in markers]
        require(all(log.count(m) == 1 for m in markers) and positions == sorted(positions), 'Host lifecycle invalid')
        require(log.count('TileXR UDMA attaching to existing RA initialization') == 1
                and 'InitUDMA success' in log, 'Explicit TileXR RA attachment missing')
        require(struct.unpack('<22i', (root / 'allreduce-results.bin').read_bytes()) == (4,) * 22, 'SUM4 result invalid')
        summary = json.loads((root / 'summary.json').read_text())
        warm = json.loads((root / 'warmup-summary.json').read_text())
        require(summary['operator_correctness'] == summary['flags_cleared'] == 'PASS'
                and summary['verified_iterations'] == 21 and warm['validated_outputs'] == 20,
                'Dispatch iteration validation invalid')
        rows.append({'rank': rank, 'operator_correctness': 'PASS', 'flags_cleared': 'PASS',
                     'host_lifecycle_verified': True, 'dispatch_iterations_checked': 21,
                     'allreduce_values_checked': 22, 'log_sha256': sha(batch / ('rank' + str(rank) + '.log'))})
    exits = json.loads((batch / 'process-exits.json').read_text())
    require(len(exits) == 4 and all(r['exit_code'] == 0 and not r['group_remaining'] for r in exits),
            'Rank group did not exit cleanly')
    return analysis, rows


def main():
    config_path, group_path = map(Path, sys.argv[1:])
    config, group = json.loads(config_path.read_text()), json.loads(group_path.read_text())
    run = group_path.parent
    require(run.parent.parent == config_path.parent and run.parent.name == 'groups', 'Unexpected group path')
    require(not (run / 'group-summary.json').exists() and not (run / 'launch-intent.json').exists(),
            'Existing group evidence; inspect without replay')
    require(group['variant'] in ('baseline', 'f9') and group['case'] in CASES
            and config['mode'] in ('correctness', 'capture') and group['tokens'] == CASES[group['case']],
            'Group outside authorized A/B plan')
    require(not any(os.environ.get(k) for k in ('ASCEND_VISIBLE_DEVICES', 'ASCEND_RT_VISIBLE_DEVICES', 'LD_PRELOAD')),
            'Unexpected device remap or preload')
    require(os.environ.get('TILEXR_UDMA_ATTACH_EXISTING_RA') == '1', 'Explicit RA attachment not enabled')
    result = {'schema': 'tilelang_107_f9_host107_group.v1', 'task_id': config['task_id'], 'revision': REV,
              **group, 'status': 'FAIL_OR_UNVERIFIED', 'npu_launch': False, 'clock': 'NOT_VERIFIED',
              'devices': [0, 1, 2, 3], 'cores': [64] * 4, 'warmup': 20, 'measured': 1,
              'profiling': config['mode'] == 'capture', 'pre_allreduce': 'allreduce'}
    try:
        result['integrity_before'] = integrity(config)
        inventory = config_path.parent / 'env_list.json'
        checker = config_path.parent / 'check_npu_status.py'
        require(sha(checker) == config['checker_sha256'], 'Occupancy checker changed')
        require(json.loads(inventory.read_text()) == {'version': 1, 'environments':
                [{'name': 'a5-107', 'ssh': 'local', 'devices': [0, 1, 2, 3]}]}, 'Occupancy inventory changed')
        p = subprocess.run(['/usr/bin/python3', str(checker), str(inventory)], capture_output=True, timeout=25)
        (run / 'occupancy-before.stdout.log').write_bytes(p.stdout)
        (run / 'occupancy-before.stderr.log').write_bytes(p.stderr)
        require(p.returncode == 0 and json.loads(p.stdout)['safe_to_use'] is True,
                'Selected devices busy or occupancy unknown; stop')
        result['health_before'] = query_all('health', run, 'before')
        require(all(r['ok'] for r in result['health_before']), 'NPU health not OK; stop')
        result['port_gate'] = port_gate(group['hccl_base_port'], group['comm_port'])
        os.environ['HCCL_IF_BASE_PORT'] = str(group['hccl_base_port'])
        sys.path.insert(0, str(EXP / group['variant'] / 'TileXR/examples'))
        from tilelang_dispatch.run_native import run as native_run
        args = SimpleNamespace(install=EXP / group['variant'] / 'builds' / ('t' + str(group['tokens'])) / 'install',
                               fixture=EXP / 'fixture' / group['case'], output=run / 'native',
                               devices=[0, 1, 2, 3], cores=[64] * 4, comm_port=group['comm_port'],
                               mode=config['mode'], rounds=1, warmup=20, timeout=240, pre_allreduce='allreduce')
        save(run / 'launch-intent.json', {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'status': 'STARTING_FRESH_FOUR_RANK_GROUP', 'npu_launch': None, **group})
        result['npu_launch'] = None
        execution = native_run(args)
        require(execution['status'] == 'PASS' and len(execution['rounds']) == 1, 'Native rank execution failed')
        analysis, rows = validate_native(args.output, args.fixture,
                                        EXP / group['variant'] / 'generated' / ('t' + str(group['tokens'])),
                                        result['profiling'])
        save(run / 'analysis.json', analysis)
        save(run / 'rank-validation.json', rows)
        result.update(status='PASS', npu_launch=True, ranks=rows, analysis_sha256=sha(run / 'analysis.json'),
                      rank_validation_sha256=sha(run / 'rank-validation.json'))
    except BaseException as error:
        result.update(error_type=type(error).__name__, error_detail=str(error)[:300])
    finally:
        try:
            # Allow driver accounting to settle after our verified/exited children; do not launch another group yet.
            deadline = time.monotonic() + 20
            attempt = 0
            while True:
                result['occupancy_after'] = query_all('proc-mem', run, 'after' + str(attempt))
                if all(r['idle'] for r in result['occupancy_after']) or time.monotonic() >= deadline:
                    break
                time.sleep(1)
                attempt += 1
            require(all(r['idle'] for r in result['occupancy_after']), 'Post-group occupancy not idle')
            result['health_after'] = query_all('health', run, 'after')
            require(all(r['ok'] for r in result['health_after']), 'Post-group health not OK')
            result['integrity_after'] = integrity(config)
        except BaseException as error:
            result.update(status='FAIL_OR_UNVERIFIED', finalization_error_type=type(error).__name__,
                          finalization_error_detail=str(error)[:300])
        save(run / 'group-summary.json', result)
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
