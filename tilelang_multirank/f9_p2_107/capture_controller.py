"""Run a fixed plan serially; stop at the first failed/unknown group."""
from pathlib import Path
import hashlib
import json
import subprocess
import shutil
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
    assert config['mode'] == 'capture'
    tokens = config['tokens']
    assert tokens == 256 and config['phase'] == 'capture-t256'
    selected = [g for g in plan['groups'] if g['phase'] == config['phase']]
    assert len(selected) == 10 and [g['index'] for g in selected] == list(range(4,14))
    assert [g['round'] for g in selected] == list(range(1,11))
    assert all(g['tokens'] == 256 and g['case'] == 'p2-e4-t256-balanced'
               and g['mode'] == 'capture' and g['variant'] == 'f9' for g in selected)
    result = {'schema': 'tilelang_f9_p2_107_capture.v1', 'task_id': config['task_id'],
              'revision': config['revision'], 'phase': config['phase'], 'target': '141.61.73.107',
              'devices': [0, 1], 'cores': [64, 64], 'status': 'FAIL_OR_UNVERIFIED',
              'npu_launch': False, 'official_hccl_launch': False, 'clock': 'NOT_VERIFIED',
              'tokens': tokens, 'groups': [], 'artifact_manifest_sha256': config['artifact_manifest_sha256'],
              'provenance_sha256': config['provenance_sha256']}
    deadline = time.monotonic() + 3400
    try:
        profiler = shutil.which('msprof')
        assert profiler, 'msprof required; no capture fallback'
        profiler = Path(profiler).resolve(strict=True)
        assert profiler.is_relative_to(Path('/usr/local/Ascend/cann-9.1.0').resolve()), 'Profiler outside pinned CANN'
        profiler_sha = sha(profiler)
        result['profiler_identity'] = {'path': str(profiler), 'sha256': profiler_sha}
        (run / 'groups').mkdir(exist_ok=False)
        for group in selected:
            assert sha(profiler) == profiler_sha, 'Profiler changed'
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
            assert summary['profiling'] is True and summary['mode'] == 'capture'
            assert sha(directory / 'analysis.json') == summary['analysis_sha256']
            analysis = json.loads((directory / 'analysis.json').read_text())
            assert analysis['status'] == 'PASS' and analysis['capture_checked'] is True
            assert analysis['mode'] == 'capture' and analysis['rounds'] == 1 and analysis['warmup'] == 20
            assert len(analysis['rank_results']) == 2
            for rank, row in enumerate(analysis['rank_results']):
                assert row['rank'] == rank and row['cores'] == 64 and len(row['captures']) == 1
                capture = row['captures'][0]
                assert capture['capture_structure'] == 'PASS' and capture['operator_correctness'] == 'PASS'
                assert capture['performance_sample_accepted'] is True and len(capture['lanes']) == 64
                assert capture['unit'] == 'ticks' and capture['cycles_per_us'] is None
                assert 'F9.payload_clear' in capture['stages'] and 'F9' in capture['functions']
                assert len(row['device_task_per_round_us']) == len(row['acl_event_per_round_us']) == 1
            assert len(summary['occupancy_after']) == 2 and all(r['idle'] for r in summary['occupancy_after'])
            assert sha(profiler) == profiler_sha, 'Profiler changed during group'
            evidence = {}
            for rank in range(2):
                root = directory / 'native' / 'round1' / ('rank' + str(rank))
                for name in ('round20.dispatch.records', 'round20.dispatch.diagnostics.bin',
                             'timing.csv', 'allreduce.csv', 'host-timeline.csv',
                             'native-barriers.csv', 'native-schedule.json'):
                    file = root / name
                    evidence[str(file.relative_to(directory))] = sha(file)
                prof = directory / 'native' / 'round1' / ('prof-rank-' + str(rank))
                csvs = list(prof.rglob('op_summary*.csv'))
                assert len(csvs) == 1
                evidence[str(csvs[0].relative_to(directory))] = sha(csvs[0])
                mapping = Path('/root/tilelang-107-f9-p2-b520293d-20261116-run01') / group['variant'] / 'generated' / ('t' + str(tokens)) / ('rank' + str(rank)) / 'dispatch.ticks.json'
                evidence[str(mapping)] = sha(mapping)
            evidence['native/run.json'] = sha(directory / 'native' / 'run.json')
            evidence['analysis.json'] = sha(directory / 'analysis.json')
            save(directory / 'capture-evidence.json', evidence)
            result['groups'].append({'group': key, **group, 'status': 'PASS',
                                     'summary_sha256': sha(directory / 'group-summary.json'),
                                     'analysis_sha256': summary['analysis_sha256'],
                                     'capture_evidence_sha256': sha(directory / 'capture-evidence.json')})
            save(run / 'phase-progress.json', {'state': 'GROUP_PASS', 'current_group': key,
                                             'completed_groups': len(result['groups'])})
        result.pop('current_group', None)
        result.pop('first_group_status', None)
        result.update(status='PASS', npu_launch=True, cases=1, fresh_rank_groups=10,
                      variants=['f9'], measured_groups_per_variant=10,
                      rank_receipts=20, rank_dispatch_iterations=420, allreduce_values_checked=440,
                      rank_captures=20, profiling=True, warmup_per_group=20, measured_per_group=1,
                      sampling='ten serial fresh P2 groups, one F9 variant',
                      numeric_export='NOT_EXPORTED; saved full analysis contains per-core stage/workload values',
                      primary_time_source='msprof device task; ACL and internal raw ticks reported separately')
    except BaseException as error:
        result.update(error_type=type(error).__name__, error_detail=str(error)[:300])
        if result.get('current_group') and (run / 'groups' / result['current_group'] / 'launch-intent.json').exists():
            result['npu_launch'] = None
    save(run / 'worker-summary.json', result)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
