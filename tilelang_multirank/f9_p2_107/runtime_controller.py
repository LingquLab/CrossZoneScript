"""Run a fixed plan serially; stop at the first failed/unknown group."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def main():
    config_path = Path(sys.argv[1])
    run = config_path.parent
    config = json.loads(config_path.read_text())
    plan = json.loads((run / 'group-plan.json').read_text())
    assert sha(run / 'group-plan.json') == config['group_plan_sha256']
    assert sha(run / 'runtime_group.py') == config['group_worker_sha256']
    assert config['phase'] == 'f9-correctness' and config['mode'] == 'correctness'
    selected = [g for g in plan['groups'] if g['phase'] == config['phase']]
    assert len(selected) == 3 and {g['variant'] for g in selected} == {'f9'}
    assert {g['case'] for g in selected} == {'p2-e4-t256-balanced'}
    assert [g['index'] for g in selected] == [1,2,3] and [g['round'] for g in selected] == [1,2,3]
    assert all(g['mode'] == 'correctness' and g['tokens'] == 256 for g in selected)
    result = {'schema': 'tilelang_f9_p2_107_correctness.v1', 'task_id': config['task_id'],
              'revision': config['revision'], 'phase': config['phase'], 'target': '141.61.73.107',
              'devices': [0, 1], 'cores': [64, 64], 'status': 'FAIL_OR_UNVERIFIED',
              'npu_launch': False, 'official_hccl_launch': False, 'clock': 'NOT_VERIFIED',
              'groups': [], 'artifact_manifest_sha256': config['artifact_manifest_sha256'],
              'provenance_sha256': config['provenance_sha256']}
    deadline = time.monotonic() + 840
    try:
        (run / 'groups').mkdir(exist_ok=False)
        for group in selected:
            assert time.monotonic() < deadline, 'Phase time budget exhausted; do not replay completed groups'
            key = 'g' + str(group['index']).zfill(3)
            directory = run / 'groups' / key
            directory.mkdir(exist_ok=False)
            save(directory / 'group.json', group)
            result['current_group'] = key
            save(run / 'phase-progress.json', {'state': 'PREPARING_GROUP', 'current_group': key,
                                             'group': group, 'completed_groups': len(result['groups'])})
            with (directory / 'worker.stdout.log').open('xb') as out, (directory / 'worker.stderr.log').open('xb') as err:
                p = subprocess.Popen(['/usr/bin/python3', str(run / 'runtime_group.py'),
                                      str(config_path), str(directory / 'group.json')],
                                     stdin=subprocess.DEVNULL, stdout=out, stderr=err)
                ticks = None
                try:
                    ticks = Path('/proc/' + str(p.pid) + '/stat').read_text().split(') ', 1)[1].split()[19]
                except FileNotFoundError:
                    pass
                save(directory / 'worker-started.json', {'pid': p.pid, 'start_ticks': ticks})
                # Namespace parent owns the bounded process tree and detached native rank groups.
                code = p.wait(timeout=min(330, max(1, deadline - time.monotonic())))
            save(directory / 'worker-exit.json', {'exit_code': code})
            if (directory / 'launch-intent.json').exists():
                result['npu_launch'] = None
            assert (directory / 'group-summary.json').exists(), 'Group has no final receipt: ' + key
            summary = json.loads((directory / 'group-summary.json').read_text())
            assert summary['task_id'] == result['task_id'] and summary['index'] == group['index']
            result['first_group_status'] = summary['status']
            if summary.get('npu_launch') is True:
                result['npu_launch'] = True
            assert code == 0 and summary['status'] == 'PASS', 'First failing group: ' + key
            assert len(summary['ranks']) == 2 and all(r['host_lifecycle_verified'] for r in summary['ranks'])
            assert len(summary['occupancy_after']) == 2 and all(r['idle'] for r in summary['occupancy_after'])
            result['groups'].append({'group': key, **group, 'status': 'PASS',
                                     'summary_sha256': sha(directory / 'group-summary.json'),
                                     'analysis_sha256': summary['analysis_sha256']})
            save(run / 'phase-progress.json', {'state': 'GROUP_PASS', 'current_group': key,
                                             'completed_groups': len(result['groups'])})
        result.pop('current_group', None)
        result.pop('first_group_status', None)
        result.update(status='PASS', npu_launch=True, cases=1, fresh_rank_groups=3,
                      rank_receipts=6, rank_dispatch_iterations=126, allreduce_values_checked=132,
                      profiling=False, warmup_per_group=20, measured_per_group=1)
    except BaseException as error:
        result.update(error_type=type(error).__name__, error_detail=str(error)[:300])
        if result.get('current_group') and (run / 'groups' / result['current_group'] / 'launch-intent.json').exists():
            result['npu_launch'] = None
    save(run / 'worker-summary.json', result)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
