"""Pin the successful build, gate current resources, then run three fresh groups."""
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path('/root/tilelang-107-general-64ed5828-20261117-run02')
sys.path.insert(0, str(ROOT))
from operations import gate, integrity, require, save, sha

BUILD_SHA = '4e34d3a988b2d6219ea30e934a462c9207b894cf44b6e33b4d7295792b8c4c5c'
REVISION = 'dce8baa3572fd7f5b6ee31c523985f52ff9e0fe3'


def prerequisite():
    integrity()
    require(sha(ROOT / 'build-summary.json') == BUILD_SHA, 'Build receipt changed')
    build = json.loads((ROOT / 'build-summary.json').read_text())
    require(build['status'] == 'PASS' and build['experiment_revision'] == REVISION,
            'Wrong experiment build')
    for name, digest in build['artifacts'].items():
        require(sha(ROOT / name) == digest, 'Build artifact changed: ' + name)
    for name, digest in json.loads((ROOT / 'payload-manifest.json').read_text())['files'].items():
        require(sha(ROOT / name) == digest, 'Phase support changed: ' + name)
    require(not any((ROOT / p).exists() for p in ('correctness', 'correctness-launch', 'capture', 'capture-launch')),
            'Saved device phase exists; audit before retry')
    return build


def main():
    build = prerequisite()
    directory = ROOT / 'correctness-entry01'
    directory.mkdir(exist_ok=False)
    result = {'schema': 'tilelang_general_107_correctness.v1', 'status': 'FAIL_OR_UNVERIFIED',
              'candidate': build['candidate'], 'experiment_revision': REVISION,
              'phase': 'correctness', 'devices': [0, 1], 'cores': [64, 64],
              'build_summary_sha256': BUILD_SHA, 'clock': 'NOT_VERIFIED', 'npu_launch': False}
    try:
        ports = [port for i in range(13) for port in (*range(27000 + i * 64, 27032 + i * 64),
                 29400 + i * 3, 29401 + i * 3, 29402 + i * 3)]
        initial = gate(directory / 'initial-gate', all_devices=True, ports=ports)
        result['initial_gate'] = {'status': initial['status'], 'utc': initial['utc'],
                                  'ports_checked': initial['ports_checked'], 'snapshot_only': True}
        # The parent enforces a private mount view, bounded lifetime and exact owned-child cleanup.
        with (directory / 'parent.stdout.log').open('xb') as out, (directory / 'parent.stderr.log').open('xb') as err:
            process = subprocess.run(['/usr/bin/python3', str(ROOT / 'phase_parent.py'), 'correctness'],
                                     stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=1050)
        save(directory / 'parent.receipt.json', {'exit_code': process.returncode})
        path = ROOT / 'correctness-launch/summary.json'
        child = json.loads(path.read_text())
        result.update(parent=child, parent_summary_sha256=sha(path), npu_launch=child.get('npu_launch'))
        integrity()
        require(process.returncode == 0 and child['status'] == 'PASS' and len(child['groups']) == 3,
                'Three fresh correctness groups did not pass')
        result.update(status='PASS', warmup_per_group=20, measured_per_group=1,
                      fresh_rank_groups=3, rank_receipts=6, allreduce_values_checked=132,
                      runtime_validation='THREE_FRESH_CORRECTNESS_PASS', original_configuration_preserved=True)
    except Exception as error:
        result.update(error_type=type(error).__name__, error_detail=str(error)[:350])
        result['npu_launch'] = any((ROOT / 'correctness').glob('g*/launch-intent.json'))
    save(directory / 'summary.json', result)
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
