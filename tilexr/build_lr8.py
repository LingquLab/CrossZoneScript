"""Follow the guide's source/build/ABI/inspection phases; keep bulk logs local."""
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr'
REPORT = ROOT / 'reports' / 'lr8-1'


def save(name, value):
    (REPORT / name).write_text(json.dumps(value, indent=2), encoding='utf-8')


def execute(name, argv, timeout, input_bytes=None):
    save('progress.json', {'phase': name, 'state': 'running'})
    with (REPORT / (name + '.stdout.log')).open('wb') as out, (REPORT / (name + '.stderr.log')).open('wb') as err:
        result = subprocess.run(list(map(str, argv)), input=input_bytes, stdout=out, stderr=err, timeout=timeout)
    if result.returncode:
        save('progress.json', {'phase': name, 'state': 'failed', 'exit_code': result.returncode})
        raise RuntimeError('Phase failed: ' + name + '; inspect saved green-local evidence before retrying')


def main():
    workspace = json.loads((ROOT / 'workspace.json').read_text(encoding='utf-8'))
    REPORT.mkdir(parents=True, exist_ok=False)
    ssh = ['ssh', '-T', '-i', 'C:/Users/z00813871/.ssh/id_ed25519', '-p', '22',
           '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
           '-o', 'ConnectTimeout=12', 'root@141.61.94.26', 'timeout 150 python3 -']
    execute('create-container', ssh, 165, Path(__file__).with_name('create_simulator.py').read_bytes())
    target = json.loads((REPORT / 'create-container.stdout.log').read_text(encoding='utf-8'))
    save('container.json', target)
    target_file = REPORT / 'container.json'
    stage = '/work/lr8-1'
    tool = Path(workspace['checkouts']['tool'])
    execute('sources', [sys.executable, tool / 'tools/simulator/sources.py',
        '--repository', workspace['checkouts']['runtime'], '--revision', workspace['versions']['runtime'],
        '--tooling-revision', workspace['versions']['generator'], '--request', target_file,
        '--inventory', workspace['inventory'], '--stage-root', stage, '--local-output', REPORT / 'source'], 240)
    source = json.loads((REPORT / 'source/result.json').read_text(encoding='utf-8'))
    assert source['state'] == 'candidate_sources_staged_not_built'
    common = ['--inventory', workspace['inventory'], '--cluster', target['cluster'], '--host', target['host'],
        '--hostname', target['hostname'], '--container', target['container'], '--container-id', target['container_id'],
        '--image-id', target['image_id'], '--sources', source['sources'], '--output', stage + '/kernel',
        '--runtime-commit', source['runtime_commit'], '--source-lock-sha256', source['source_lock_sha256'],
        '--cann-root', '/opt/sim/Ascend/cann-9.1.0', '--driver-root', '/usr/local/Ascend/driver',
        '--cann-env', '/opt/sim/cann-env.sh', '--cann-env-sha256', target['cann_env_sha256'],
        '--ubus-library', target['ubus_library'], '--ubus-sha256', target['ubus_sha256'],
        '--sim-variant', 'sim-split1', '--timeout', '450']
    cli = [sys.executable, tool / 'scripts/run_alltoall_sim_kernel.py']
    states = {}
    phases = [('build', ['--kernel-name', 'urma_lr8', '--stage-profiling', '0'], 'verified_not_simulated'),
              ('abi', [], 'host_abi_verified_not_simulated'),
              ('inspect', ['--library-dir', '/opt/sim/plugins', '--inspection-output', stage + '/environment'],
               'resources_verified_not_simulated')]
    for phase, extra, expected in phases:
        execute(phase, cli + [phase] + common + extra + ['--local-output', REPORT / phase], 495)
        value = json.loads((REPORT / phase / 'result.json').read_text(encoding='utf-8'))
        assert value['state'] == expected
        states[phase] = value['state']
    request = json.loads((REPORT / 'build/request.json').read_text(encoding='utf-8'))
    request['environment_report'] = stage + '/environment'
    save('current-request.json', request)
    summary = {'state': 'lr8_built_abi_and_environment_verified_not_simulated', 'report': str(REPORT),
               'container': target, 'phases': states, 'source_lock_sha256': source['source_lock_sha256']}
    save('progress.json', {'phase': 'complete', 'state': 'passed'})
    save('summary.json', summary)
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == '__main__':
    main()
