"""Run statically verified 64P suites with the same extended profiling adapter."""

import hashlib
import json
import math
import subprocess
import sys

from prepare_64p import CAMPAIGN, KERNELS, TOOL_COMMIT
from run_profiled_points import IDENTITY, ROOT


def load_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


def run_prepared_points(elements, references, simulation_timeout):
    preparation = ROOT / 'reports' / CAMPAIGN
    prepared = load_json(preparation / 'prepared-cases.json')
    assert load_json(preparation / 'progress.json') == {'state': 'prepared', 'prepared_cases': 16}
    tool = ROOT / 'TileXR-tool-64p-1'
    revision = subprocess.check_output(['git', '-C', str(tool), 'rev-parse', 'HEAD'], timeout=30).decode().strip()
    assert revision == TOOL_COMMIT
    adapter = tool / 'tools/simulator/suite.py'
    adapter_sha = hashlib.sha256(adapter.read_text(encoding='utf-8').encode()).hexdigest()
    workspace = load_json(ROOT / 'workspace.json')
    campaign = f'profiled-64p-e{elements}-1'
    report = ROOT / 'reports' / campaign
    report.mkdir(exist_ok=False)
    results = []
    for kernel, reference in references.items():
        assert kernel in KERNELS
        proof, = [case for case in prepared if case['kernel'] == kernel and case['elements_per_peer'] == elements]
        label = f'{kernel}-64p-e{elements}'
        suite = f'/work/{CAMPAIGN}/{label}'
        assert proof['state'] == 'suite_prepared_not_simulated' and proof['suite'] == suite
        assert proof['rank_size'] == 64 and proof['profiling_capacity_per_stream'] == 512
        request = load_json(preparation / label / 'prepare/request.json')
        assert request['mode'] == 'prepare' and request['suite_output'] == suite
        assert request['rank_size'] == 64 and request['elements_per_peer'] == elements
        assert request['stage_profiling'] == 1 and request['suite_adapter_sha256'] == adapter_sha
        local = report / label
        local.mkdir()
        command = [sys.executable, str(tool / 'scripts/run_alltoall_sim_kernel.py'), 'run',
                   '--inventory', workspace['inventory'], '--suite-output', suite, '--rank-size', '64',
                   '--elements-per-peer', str(elements), '--stage-profiling', '1', '--threads', '8',
                   '--simulation-timeout', str(simulation_timeout), '--timeout', str(simulation_timeout + 300),
                   '--local-output', str(local / 'run')]
        for name in IDENTITY:
            command += ['--' + name.replace('_', '-'), request[name]]
        progress = {'case': label, 'phase': 'run', 'state': 'running', 'completed_cases': len(results)}
        save_json(report / 'progress.json', progress)
        with (local / 'run.log').open('wb') as log:
            process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     timeout=simulation_timeout + 360)
        if process.returncode:
            progress.update(state='failed_or_unknown', exit_code=process.returncode)
            save_json(report / 'progress.json', progress)
            raise RuntimeError('64P run failed: ' + label)
        assert load_json(local / 'run/result.json')['state'] == 'simulation_outputs_verified'
        row, = load_json(local / 'run/transport.json')
        evidence, = [json.loads(line.removeprefix('SST_RESULT=')) for line in row['stdout'].splitlines()
                     if line.startswith('SST_RESULT=')]
        assert evidence['execution_adapter_sha256'] == adapter_sha
        assert evidence['state'] == 'simulation_outputs_verified' and evidence['runner_returncode'] == 0
        assert evidence['remaining_processes'] == []
        assert evidence['loaded_libraries']['state'] == 'sst_library_mappings_verified'
        profiling = evidence['stage_profiling']
        assert profiling['state'] == 'stage_profiling_verified' and profiling['rank_count'] == 64
        assert profiling['stream_count'] == 64 * proof['streams_per_rank']
        simulated = evidence['simulated_us']
        assert math.isfinite(simulated) and simulated > 0
        assert evidence['run_dir'].startswith(suite + '/runs/')
        results.append({'kernel': kernel, 'ranks': 64, 'elements_per_peer': elements, 'status': 'passed',
                        'simulated_us': simulated, 'reference_us': reference,
                        'difference_us': round(simulated - reference, 6) if reference is not None else None,
                        'run_dir': evidence['run_dir'], 'kernel_sha256': proof['kernel_sha256'],
                        'profiling_capacity_per_stream': 512, 'stream_count': profiling['stream_count'],
                        'dropped_records': 0, 'profiling_report': profiling['report']})
        save_json(report / 'results.json', results)
    save_json(report / 'progress.json', {'state': 'passed', 'completed_cases': len(results)})
    print(json.dumps({'campaign': campaign, 'adapter_sha256': adapter_sha, 'results': results}, ensure_ascii=True))


if __name__ == '__main__':
    run_prepared_points(128, dict(zip(KERNELS, (99.912, 102.456, 91.218, 128.515), strict=True)),
                        simulation_timeout=1800)
