"""COORD008: evidence-only gate for physical 107 devices 0/1; no ACL init."""
from pathlib import Path
import concurrent.futures
import datetime
import hashlib
import json
import os
import re
import socket
import subprocess

ROOT = Path('/root/tilelang-107-f9-p2-b520293d-20261116-run01')
OLD = Path('/root/tilelang-107-f9-host107-b520293d-20261116-run01')
QUAL = Path('/root/tilelang-hccl-qualifications/693ec683dcc80807f1ccc0c0356a6e032008fd71e1a9a1538c435eaa1a153dc6/supplements/layer0-peer-private-view-single/receipt.json')
SYSTEM = {'/etc/hccl_rootinfo.json': '56a0d1eb4c38422fbbdd946b34036ed6496fe36c1be95045d0519f35d69ad523',
          '/usr/local/Ascend/driver/topo/950/atlas_850_1.json': '65e257641adea221a576bab32710f759a59df80b50bc8ef613b38e10c6164db9'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    assert os.geteuid() == 0
    assert sha('/etc/machine-id') == '78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'
    # Exclusive creation makes an accidental replay stop before any probe.
    ROOT.mkdir(mode=0o750, exist_ok=False)
    out = ROOT / 'preflight'
    out.mkdir()
    result = {'schema': 'tilelang_f9_p2_107_preflight.v1', 'authorization': 'COORD-20261116-008',
              'status': 'FAIL_OR_UNVERIFIED', 'revision': 'b520293d5f014fcbd4897a334d50ebf9ca9c601b',
              'devices': [0, 1], 'npu_launch': False, 'acl_initialization': False,
              'clock': 'NOT_VERIFIED', 'directory': str(ROOT),
              'checked_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        result['global_configuration_preserved'] = all(sha(p) == s for p, s in SYSTEM.items())
        assert result['global_configuration_preserved'], 'Global configuration changed'
        assert sha(QUAL) == 'e22807e4b530ded459533c840eb9e77722204776f5e99f2994d5a0640fe1b609'
        q = json.loads(QUAL.read_text())
        assert q['status'] == 'PASS' and not q['own_residual_children']
        assert q['identity']['devices'] == [0, 1, 2, 3]
        result['qualification_reused'] = True
        result['qualification_device_subset'] = [0, 1]
        markers = [b'tilelang-107-f9-host107-b520293d-20261116-run01',
                   b'tilelang-107-multirank-e28d87de-20261116-run01',
                   b'bs256-p4-107-20261116', b'xr-native107-', str(ROOT).encode()]
        matches, read_errors = [], []
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit() or int(proc.name) == os.getpid():
                continue
            try:
                cmd = (proc / 'cmdline').read_bytes()
                if any(m in cmd for m in markers):
                    fields = (proc / 'stat').read_text().rsplit(') ', 1)[1].split()
                    if fields[0] != 'Z':
                        matches.append({'pid': int(proc.name), 'state': fields[0], 'start_ticks': fields[19],
                                        'cmdline_sha256': hashlib.sha256(cmd).hexdigest()})
            except (FileNotFoundError, ProcessLookupError):
                pass
            except OSError:
                read_errors.append(int(proc.name))
        result['known_task_process_matches'] = matches
        result['process_read_error_count'] = len(read_errors)
        assert not matches and not read_errors, 'Known task active or process evidence indeterminate'
        inventory = {'version': 1, 'environments': [{'name': 'a5-107-p2', 'ssh': 'local', 'devices': [0, 1]}]}
        (out / 'env_list.json').write_text(json.dumps(inventory))
        checker = OLD / 'f9-correctness-20261116-01/check_npu_status.py'
        expected_checker = '7b7b4658bc974bf43b333756a1ac4cf590e73e9fa37139d0de9e59202d96e0b9'
        assert sha(checker) == expected_checker, 'Retained occupancy checker changed'
        p = subprocess.run(['/usr/bin/python3', str(checker), str(out / 'env_list.json')],
                           capture_output=True, timeout=30)
        (out / 'occupancy.stdout.log').write_bytes(p.stdout)
        (out / 'occupancy.stderr.log').write_bytes(p.stderr)
        result['occupancy'] = {'exit_code': p.returncode, 'safe_to_use': json.loads(p.stdout)['safe_to_use']}
        assert p.returncode == 0 and result['occupancy']['safe_to_use'], 'Selected devices busy or unknown'

        def health(device):
            p = subprocess.run(['npu-smi', 'info', '-t', 'health', '-i', str(device)],
                               capture_output=True, timeout=20)
            (out / ('health-device%d.stdout.log' % device)).write_bytes(p.stdout)
            (out / ('health-device%d.stderr.log' % device)).write_bytes(p.stderr)
            return {'device': device, 'exit_code': p.returncode,
                    'ok': p.returncode == 0 and bool(re.search(rb'Health Status\s*:\s*OK\s*$', p.stdout, re.M))}

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            result['health'] = list(pool.map(health, [0, 1]))
        assert all(x['ok'] for x in result['health']), 'Selected device health not OK'
        ephemeral = list(map(int, Path('/proc/sys/net/ipv4/ip_local_port_range').read_text().split()))
        plans = [{'group_index': i + 1, 'hccl_base_port': 24000 + i * 64, 'comm_port': 29000 + i} for i in range(13)]
        ports = [p for row in plans for p in [*range(row['hccl_base_port'], row['hccl_base_port'] + 32), row['comm_port']]]
        assert len(set(ports)) == len(ports) and all(not ephemeral[0] <= p <= ephemeral[1] for p in ports)
        for port in ports:
            for family, address in ((socket.AF_INET, '0.0.0.0'), (socket.AF_INET6, '::')):
                with socket.socket(family, socket.SOCK_STREAM) as sock:
                    if family == socket.AF_INET6:
                        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    sock.bind((address, port))
        (out / 'port-plan.json').write_text(json.dumps(plans, indent=2) + '\n')
        result['ports'] = {'groups': 13, 'distinct_ports': len(ports), 'ipv4_ipv6_bind': 'PASS',
                           'snapshot_only': True, 'ephemeral_range': ephemeral}
        result['core_count'] = 'PENDING_LIVE_QUERY_AFTER_IDLE_GATE_BEFORE_BUILD'
        result['status'] = 'PASS'
    except Exception as error:
        result.update(error_type=type(error).__name__, error_detail=str(error)[:180])
    (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, separators=(',', ':'), ensure_ascii=True))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
