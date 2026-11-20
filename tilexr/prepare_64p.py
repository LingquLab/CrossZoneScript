"""Generate and statically verify the four supported 64P kernel variants."""

import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

from run_profiled_points import IDENTITY, ROOT, SSH


CAMPAIGN = 'prepare-64p-1'
HOST_CAMPAIGN = '/root/crosszone-tilexr-acc8c509/' + CAMPAIGN
TOOL_COMMIT = '96581a23de5e7e87ce90c770fa130a596600944e'
BASE_ADAPTER_SHA256 = '9c9906816b58b0186cdae72d18e6b45b31012eca2ae036861127f9275d1c35bf'
KERNELS = ('urma_lr8', 'urma_cw', 'urma_lr8_p16_s16_local7', 'urma_lr8_p16_s8_local7')
ELEMENTS = (128, 2048, 16384, 65536)
LIMIT_BYTES = 6 * 1024**3


def extend_adapter(source):
    if hashlib.sha256(source.encode()).hexdigest() != BASE_ADAPTER_SHA256:
        raise ValueError('Unexpected SHA256 for pinned suite adapter')
    return (source.replace('PROFILE_STREAMS, PROFILE_CAPACITY = 33, 256',
                           'PROFILE_STREAMS, PROFILE_CAPACITY = 33, 512')
                  .replace('    limit = 1024 ** 3',
                           '    limit = 6 * 1024 ** 3 if ranks == 64 else 1024 ** 3')
                  .replace('    budget = 1024 ** 3 - ranks *',
                           "    budget = resources_budget['limit_bytes'] - ranks *"))


def main():
    workspace = json.loads((ROOT / 'workspace.json').read_text(encoding='utf-8'))
    assert workspace['versions']['tool'] == TOOL_COMMIT
    report = ROOT / 'reports' / CAMPAIGN
    report.mkdir(exist_ok=False)
    tool = ROOT / 'TileXR-tool-64p-1'
    subprocess.run(['git', '-C', workspace['checkouts']['tool'], 'worktree', 'add', '--detach',
                    str(tool), TOOL_COMMIT], capture_output=True, check=True, timeout=180)
    adapter = tool / 'tools/simulator/suite.py'
    adapter.write_bytes(extend_adapter(adapter.read_text(encoding='utf-8')).encode())
    adapter_sha = hashlib.sha256(adapter.read_bytes()).hexdigest()
    reserve = f"from pathlib import Path\nPath({HOST_CAMPAIGN!r}).mkdir(exist_ok=False)\n"
    subprocess.run(SSH + ['timeout 20 python3 -'], input=reserve.encode(), capture_output=True, check=True, timeout=35)
    results = []
    for elements in ELEMENTS:
        for kernel in KERNELS:
            prepare_case(workspace, report, tool, adapter_sha, results, kernel, elements)
    (report / 'progress.json').write_text(json.dumps({'state': 'prepared', 'prepared_cases': len(results)}), encoding='utf-8')
    print(json.dumps({'state': 'prepared_not_simulated', 'tool_checkout': str(tool),
                      'base_tool_commit': TOOL_COMMIT, 'base_adapter_sha256': BASE_ADAPTER_SHA256,
                      'adapter_sha256': adapter_sha, 'report': str(report), 'cases': results}, ensure_ascii=True))


def prepare_case(workspace, report, tool, adapter_sha, results, kernel, elements):
    label = f'{kernel}-64p-e{elements}'
    local = report / label
    local.mkdir()
    qualification = ROOT / 'reports/matrix-build-2' / kernel
    request = json.loads((qualification / 'current-request.json').read_text(encoding='utf-8'))
    assert request['runtime_commit'] == workspace['versions']['runtime'] and request['stage_profiling'] == 1
    suite = f'/work/{CAMPAIGN}/{label}'
    progress = {'case': label, 'state': 'preparing', 'prepared_cases': len(results)}
    (report / 'progress.json').write_text(json.dumps(progress), encoding='utf-8')
    # Check disk before writing this case, including all logical input and workspace bytes.
    disk_check = (f'import shutil\nassert shutil.disk_usage({HOST_CAMPAIGN!r}).free > {LIMIT_BYTES}\n')
    subprocess.run(SSH + ['timeout 20 python3 -'], input=disk_check.encode(),
                   capture_output=True, check=True, timeout=35)
    cli = [sys.executable, str(tool / 'scripts/run_alltoall_sim_kernel.py'), 'prepare',
           '--inventory', workspace['inventory'], '--suite-output', suite, '--rank-size', '64',
           '--elements-per-peer', str(elements), '--stage-profiling', '1', '--fullmesh-routing', 'with-clos',
           '--timeout', '1500', '--local-output', str(local / 'prepare')]
    for name in IDENTITY:
        cli += ['--' + name.replace('_', '-'), request[name]]
    with (local / 'prepare.log').open('wb') as log:
        process = subprocess.run(cli, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=1550)
    if process.returncode:
        progress.update(state='failed_or_unknown', exit_code=process.returncode)
        (report / 'progress.json').write_text(json.dumps(progress), encoding='utf-8')
        raise RuntimeError('64P preparation failed: ' + label)
    evidence = json.loads((local / 'prepare/result.json').read_text(encoding='utf-8'))
    assert evidence['state'] == 'suite_prepared_not_simulated'
    check = shlex.join(['timeout', '60', 'docker', 'exec', '-i', request['container'], 'python3', '-',
                       suite, str(elements), adapter_sha])
    checked = subprocess.run(SSH + [check], input=Path(__file__).with_name('verify_64p_case.py').read_bytes(),
                             capture_output=True, timeout=75)
    if checked.returncode:
        (local / 'case-check.stderr.log').write_bytes(checked.stderr)
        raise RuntimeError('64P allocation verification failed: ' + label)
    result = json.loads(checked.stdout)
    result.update(kernel=kernel, elements_per_peer=elements)
    (local / 'case-check.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    results.append(result)
    (report / 'prepared-cases.json').write_text(json.dumps(results, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
