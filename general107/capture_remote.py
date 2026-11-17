"""Run ten fresh profile groups only after the pinned three correctness receipts."""
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path('/root/tilelang-107-general-64ed5828-20261117-run02')
sys.path.insert(0, str(ROOT))
from operations import CANN, gate, integrity, require, save, sha

BUILD_SHA = '4e34d3a988b2d6219ea30e934a462c9207b894cf44b6e33b4d7295792b8c4c5c'
REVISION = 'dce8baa3572fd7f5b6ee31c523985f52ff9e0fe3'
CORRECTNESS_PARENT_SHA = 'da75e73683e7f1cf4d318e4741010934e7d6546163c35520bb053de584a4a132'
CORRECTNESS_WORKER_SHA = 'c8a060a0c1024bb742c7dd6f8d8dce4bf230aae442c72ecdf8f66243a278c309'


def prerequisite():
    integrity()
    require(sha(ROOT / 'build-summary.json') == BUILD_SHA, 'Build receipt changed')
    build = json.loads((ROOT / 'build-summary.json').read_text())
    require(build['status'] == 'PASS' and build['experiment_revision'] == REVISION, 'Wrong experiment build')
    for name, digest in build['artifacts'].items():
        require(sha(ROOT / name) == digest, 'Build artifact changed: ' + name)
    for name, digest in json.loads((ROOT / 'payload-manifest.json').read_text())['files'].items():
        require(sha(ROOT / name) == digest, 'Phase support changed: ' + name)
    require(sha(ROOT / 'correctness-launch/summary.json') == CORRECTNESS_PARENT_SHA
            and sha(ROOT / 'correctness/summary.json') == CORRECTNESS_WORKER_SHA, 'Correctness receipts changed')
    parent = json.loads((ROOT / 'correctness-launch/summary.json').read_text())
    require(parent['status'] == 'PASS' and parent['namespace_exit'] == 0 and not parent['timed_out']
            and not parent['cleanup_actions'] and not parent['own_residual_children'], 'Correctness lifecycle failed')
    require(len(parent['groups']) == 3, 'Three correctness groups required')
    for row in parent['groups']:
        require(row['status'] == 'PASS' and sha(ROOT / 'correctness' / ('g' + str(row['index']).zfill(3)) / 'summary.json')
                == row['summary_sha256'], 'Correctness group changed')
    require(not any((ROOT / p).exists() for p in ('capture', 'capture-launch')), 'Saved capture exists; audit before retry')
    require(sha(CANN / 'tools/profiler/bin/msprof') == '75541a2535f1dc9b39484b833b0454a002d0eabd68d7673006f52cb00e73dc82',
            'Profiler identity changed')
    return build


def main():
    build = prerequisite()
    directory = ROOT / 'capture-entry01'
    directory.mkdir(exist_ok=False)
    result = {'schema': 'tilelang_general_107_capture.v1', 'status': 'FAIL_OR_UNVERIFIED',
              'candidate': build['candidate'], 'experiment_revision': REVISION, 'phase': 'capture',
              'devices': [0, 1], 'cores': [64, 64], 'clock': 'NOT_VERIFIED', 'npu_launch': False,
              'build_summary_sha256': BUILD_SHA, 'correctness_parent_sha256': CORRECTNESS_PARENT_SHA}
    try:
        ports = [port for i in range(3, 13) for port in (*range(27000 + i * 64, 27032 + i * 64),
                 29400 + i * 3, 29401 + i * 3, 29402 + i * 3)]
        initial = gate(directory / 'initial-gate', ports=ports)
        result['initial_gate'] = {'status': initial['status'], 'utc': initial['utc'],
                                  'ports_checked': initial['ports_checked'], 'snapshot_only': True}
        with (directory / 'parent.stdout.log').open('xb') as out, (directory / 'parent.stderr.log').open('xb') as err:
            process = subprocess.run(['/usr/bin/python3', str(ROOT / 'phase_parent.py'), 'capture'],
                                     stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=3450)
        save(directory / 'parent.receipt.json', {'exit_code': process.returncode})
        path = ROOT / 'capture-launch/summary.json'
        child = json.loads(path.read_text())
        result.update(parent=child, parent_summary_sha256=sha(path), npu_launch=child.get('npu_launch'))
        integrity()
        require(process.returncode == 0 and child['status'] == 'PASS' and len(child['groups']) == 10,
                'Ten fresh capture groups did not pass')
        result.update(status='PASS', warmup_per_group=20, measured_per_group=1,
                      fresh_rank_groups=10, rank_captures=20, allreduce_values_checked=440,
                      receiver_cores=[0, 1, 2, 3], runtime_validation='TEN_FRESH_CAPTURE_PASS',
                      original_configuration_preserved=True,
                      source_root=str(ROOT / 'capture'), compiled_capacity=build['compiled_capacity'])
    except Exception as error:
        result.update(error_type=type(error).__name__, error_detail=str(error)[:350])
        result['npu_launch'] = any((ROOT / 'capture').glob('g*/launch-intent.json'))
    save(directory / 'summary.json', result)
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
