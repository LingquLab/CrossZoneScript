"""Run the guide's two fresh 8P cases through the pinned UniTest controller."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr'
REPORT = ROOT / 'reports/lr8-2'


def save(name, value):
    (REPORT / name).write_text(json.dumps(value, indent=2), encoding='utf-8')


workspace = json.loads((ROOT / 'workspace.json').read_text(encoding='utf-8'))
qualification = json.loads((REPORT / 'summary.json').read_text(encoding='utf-8'))
assert qualification['state'] == 'lr8_built_abi_and_environment_verified_not_simulated'
unitest = Path(workspace['checkouts']['unitest']) / 'tools/unitest'
original_case = json.loads((unitest / 'cases/alltoall.json').read_text(encoding='utf-8'))
assert original_case['dtype'] == 'int32' and original_case['elements_per_peer'] == 129 and original_case['seed'] == 2026
results = []
for elements in [129, 128]:
    label = 'lr8-8p-' + str(elements * 4) + 'b'
    output = REPORT / ('unitest-' + label)
    assert not output.exists(), 'Result exists; inspect it rather than repeat execution'
    config_file = REPORT / (label + '.config.json')
    case_file = REPORT / (label + '.case.json')
    case = dict(original_case, id='alltoall-int32-' + str(elements), elements_per_peer=elements)
    case_file.write_text(json.dumps(case, indent=2), encoding='utf-8')
    config = {'schema': 'tilexr.unitest.sst.v1', 'tool_checkout': workspace['checkouts']['tool'],
              'tool_commit': workspace['versions']['tool'], 'request_file': str(REPORT / 'current-request.json'),
              'inventory': workspace['inventory'], 'build_report': str(REPORT / 'build'),
              'abi_report': str(REPORT / 'abi'), 'rank_size': 8,
              'suite_output': '/work/lr8-1/' + label, 'threads': 8,
              'simulation_timeout': 600, 'timeout': 900}
    config_file.write_text(json.dumps(config, indent=2), encoding='utf-8')
    command = [sys.executable, '-m', 'controller', '--case', str(case_file), '--mode', 'single',
               '--target', 'sst', '--sim-config', str(config_file), '--no-profiling', '--output-dir', str(output)]
    save('unitest-progress.json', {'case': label, 'state': 'running', 'output': str(output)})
    with (REPORT / (label + '.controller.log')).open('wb') as log:
        completed = subprocess.run(command, cwd=unitest, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, timeout=2000)
    if completed.returncode:
        save('unitest-progress.json', {'case': label, 'state': 'failed_or_unknown', 'output': str(output),
                                      'exit_code': completed.returncode})
        raise RuntimeError('UniTest failed; inspect retained evidence before any retry: ' + label)
    report = json.loads((output / 'result.json').read_text(encoding='utf-8'))
    assert report['status'] == 'passed' and report['operator_result']['status'] == 'passed'
    evidence = report['tool_result']['sst_result']
    assert evidence['state'] == 'simulation_outputs_verified' and evidence['runner_returncode'] == 0
    assert evidence['remaining_processes'] == []
    assert evidence['loaded_libraries']['state'] == 'sst_library_mappings_verified'
    simulated_us = evidence['simulated_us']
    assert math.isfinite(simulated_us) and simulated_us > 0
    result = {'case': label, 'status': report['status'], 'operator_status': report['operator_result']['status'],
              'simulated_us': simulated_us, 'run_dir': evidence['run_dir'],
              'remaining_processes': evidence['remaining_processes'], 'report': str(output), 'stage_profiling': 0}
    if elements == 128:
        result.update(historical_reference_us=25.510, difference_us=round(simulated_us - 25.510, 6))
    results.append(result)
    save('singlepoint-results.json', results)
save('unitest-progress.json', {'state': 'passed', 'completed_cases': len(results)})
print(json.dumps({'state': 'lr8_8p_singlepoints_verified', 'results': results}, ensure_ascii=True))
