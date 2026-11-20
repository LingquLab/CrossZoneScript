"""Run fresh profiled cases using the pinned prepare/run CLI and strict checks."""
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


ROOT = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr'
SSH = ['ssh', '-T', '-i', 'C:/Users/z00813871/.ssh/id_ed25519', '-o', 'BatchMode=yes',
       '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=12', 'root@141.61.94.26']
IDENTITY = ('cluster', 'host', 'hostname', 'container', 'container_id', 'image_id', 'sources', 'output',
            'runtime_commit', 'source_lock_sha256', 'cann_root', 'driver_root', 'cann_env', 'cann_env_sha256',
            'ubus_library', 'ubus_sha256', 'environment_report', 'sim_variant')


def run_points(points, campaign, simulation_timeout=600, threads=8):
    assert re.fullmatch('[a-z0-9-]{1,60}', campaign)
    workspace = json.loads((ROOT / 'workspace.json').read_text(encoding='utf-8'))
    report = ROOT / 'reports' / campaign
    report.mkdir(exist_ok=False)
    reserve = "from pathlib import Path; Path('/root/crosszone-tilexr-acc8c509/" + campaign + "').mkdir(exist_ok=False)\n"
    subprocess.run(SSH + ['timeout 20 python3 -'], input=reserve.encode(), capture_output=True, check=True, timeout=35)
    results = []
    for kernel, ranks, elements, reference in points:
        label = kernel + '-' + str(ranks) + 'p-e' + str(elements)
        local = report / label
        local.mkdir()
        qualification = ROOT / 'reports/matrix-build-2' / kernel
        request = json.loads((qualification / 'current-request.json').read_text(encoding='utf-8'))
        assert request['runtime_commit'] == '85afd1fa61669a5ef800a80bec4df93745f119c6'
        assert request['stage_profiling'] == 1
        for phase, expected in [('build', 'verified_not_simulated'), ('abi', 'host_abi_verified_not_simulated'),
                                ('inspect', 'resources_verified_not_simulated')]:
            assert json.loads((qualification / phase / 'result.json').read_text(encoding='utf-8'))['state'] == expected
        suite = '/work/' + campaign + '/' + label
        common = ['--inventory', workspace['inventory'], '--suite-output', suite,
                  '--rank-size', str(ranks), '--elements-per-peer', str(elements), '--stage-profiling', '1',
                  '--threads', str(threads), '--simulation-timeout', str(simulation_timeout), '--timeout', str(simulation_timeout + 300)]
        for name in IDENTITY:
            common += ['--' + name.replace('_', '-'), request[name]]
        cli = [sys.executable, str(Path(workspace['checkouts']['tool']) / 'scripts/run_alltoall_sim_kernel.py')]
        preflight = None
        for phase, expected in [('prepare', 'suite_prepared_not_simulated'), ('run', 'simulation_outputs_verified')]:
            progress = {'case': label, 'phase': phase, 'state': 'running', 'completed_cases': len(results)}
            (report / 'progress.json').write_text(json.dumps(progress), encoding='utf-8')
            command = cli + [phase] + common + ['--local-output', str(local / phase)]
            if phase == 'prepare':
                command += ['--fullmesh-routing', 'with-clos']
            with (local / (phase + '.log')).open('wb') as log:
                p = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, timeout=simulation_timeout + 360)
            if p.returncode:
                progress.update(state='failed_or_unknown', exit_code=p.returncode)
                (report / 'progress.json').write_text(json.dumps(progress), encoding='utf-8')
                raise RuntimeError('Profiled matrix case failed; inspect retained receipt: ' + label + ' ' + phase)
            assert json.loads((local / phase / 'result.json').read_text(encoding='utf-8'))['state'] == expected
            if phase == 'prepare':
                command = shlex.join(['timeout', '30', 'docker', 'exec', '-i', request['container'], 'python3', '-', suite])
                p = subprocess.run(SSH + [command], input=Path(__file__).with_name('read_case_contract.py').read_bytes(),
                                   capture_output=True, check=True, timeout=45)
                preflight = json.loads(p.stdout)
                assert preflight['rank_size'] == ranks and preflight['elements_per_peer'] == elements
                assert preflight['stage_profiling'] is True
                assert preflight['profiling_contract']['verdict'] == 'ok'
                assert preflight['resource_budget']['verdict'] == 'ok' and preflight['resource_budget']['remaining_bytes'] > 0
                (local / 'preflight.json').write_text(json.dumps(preflight, indent=2), encoding='utf-8')
        transport = json.loads((local / 'run/transport.json').read_text(encoding='utf-8'))
        evidence, = [json.loads(line[len('SST_RESULT='):]) for line in transport[0]['stdout'].splitlines()
                     if line.startswith('SST_RESULT=')]
        assert evidence['state'] == 'simulation_outputs_verified' and evidence['runner_returncode'] == 0
        assert evidence['remaining_processes'] == []
        assert evidence['loaded_libraries']['state'] == 'sst_library_mappings_verified'
        assert evidence['stage_profiling']['state'] == 'stage_profiling_verified'
        assert math.isfinite(evidence['simulated_us']) and evidence['simulated_us'] > 0
        entry = {'kernel': kernel, 'ranks': ranks, 'elements_per_peer': elements, 'status': 'passed',
                 'simulated_us': evidence['simulated_us'], 'reference_us': reference,
                 'difference_us': round(evidence['simulated_us'] - reference, 6), 'run_dir': evidence['run_dir'],
                 'preflight': preflight, 'profiling': evidence['stage_profiling']}
        results.append(entry)
        (report / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    (report / 'progress.json').write_text(json.dumps({'state': 'passed', 'completed_cases': len(results)}), encoding='utf-8')
    print(json.dumps({'campaign': campaign, 'results': results}, ensure_ascii=True))


if __name__ == '__main__':
    run_points([('urma_lr8', 8, 128, 25.510), ('urma_cw', 8, 128, 24.735),
                ('urma_lr8_p16_s16_local7', 8, 128, 27.335), ('urma_lr8_p16_s8_local7', 8, 128, 25.151)],
               campaign='profiled-8p-small-1')
