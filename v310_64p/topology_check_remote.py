import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT = Path('/opt/sim/simulator/Testcases')
MODE = topology_mode
NAMES = {
    'smoke8': ('urma_dispatch_8p_prefilled_smoke', 'urma_dispatch_8p_bridge_20261120_run01'),
    'init64': ('dispatch_memory_64p_top6_profile_bridge_20261119_run11_sync_ports',
               'dispatch_memory_64p_top6_profile_bridge_20261120_init01'),
}
source_name, target_name = NAMES[MODE]
source, target = ROOT/source_name, ROOT/target_name


def active_sst():
    for entry in Path('/proc').glob('[0-9]*/comm'):
        try:
            if entry.read_text().strip() not in ('sst', 'sstsim.x'):
                continue
            if (entry.parent/'stat').read_text().rsplit(')', 1)[1].split()[0] not in ('Z', 'X'):
                return True
        except FileNotFoundError:
            continue
    return False


def hash_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else hashlib.sha256(stream.read()).hexdigest()


def run_stage(name, command, limit):
    log = target/(name+'.log')
    started = time.monotonic()
    with log.open('xb') as out:
        code = subprocess.run(command, cwd=target, stdout=out, stderr=subprocess.STDOUT,
                              timeout=limit).returncode
    return code, round(time.monotonic()-started, 3), log


def copy_case():
    target.mkdir()
    for name in ('UBusSim_config', 'v310_config', 'input', 'expected', 'input_bin',
                 'workload_params', 'wqe_bins'):
        if (source/name).is_dir():
            shutil.copytree(source/name, target/name)
    for name in ('test_UBusSim.py', 'output_generator.py', 'compare_bin.py',
                 'workload.yml', 'workload_config.yml', 'tilexr_ep_dispatch_memory_kernel.o'):
        if (source/name).is_file():
            shutil.copy2(source/name, target/name)
    for path in [*target.rglob('*.yml'), *target.rglob('*.yaml')]:
        data = path.read_bytes()
        path.write_bytes(data.replace(os.fsencode(source), os.fsencode(target)))
    (target/'audited_launcher.py').write_bytes(launcher_source)


def selected_logs(path, regex):
    expression = re.compile(regex, re.I)
    with path.open(errors='replace') as stream:
        return [line.strip()[:190] for line in stream if expression.search(line)][-8:]


result = {'mode': MODE, 'case': str(target)}
created = False
try:
    assert (source/'test_UBusSim.py').is_file(), 'REFERENCE_LAUNCHER_MISSING'
    assert not active_sst(), 'OTHER_ACTIVE_SST'
    assert not target.exists(), 'RUN_DIRECTORY_ALREADY_EXISTS'
    copy_case()
    created = True
    before = {str(path.relative_to(target)): hash_file(path)
              for folder in ('UBusSim_config', 'v310_config')
              for path in sorted((target/folder).glob('*.y*ml'))}
    assert not active_sst(), 'OTHER_ACTIVE_SST'
    options = ['-n', '8'] if MODE == 'smoke8' else ['-n', '20', '--run-mode=init',
                                                     '--output-json=graph.json']
    environment = ('source /opt/sim/sst-env.sh && '
                   'export SST_PATH=/opt/sim/local/sstcore '
                   'SST_CONFIG_FILE_PATH=/opt/sim/simulator/config/sst-plugins.conf '
                   'SST_CONFIG_FILE_PATH_SEPARATOR=: && ulimit -n 524288 && '
                   'exec timeout -k 10s 300s sst "$@" ./audited_launcher.py')
    result['phase'] = 'sst'
    result['sst_exit'], result['sst_wall_seconds'], log = run_stage(
        'run', ['bash','-lc',environment,'launch',*options], 320)
    result['completion'] = selected_logs(log, 'Simulation is complete')
    result['errors'] = selected_logs(log, r'FATAL|Error:|Exception:|dumped core|Unknown low level')
    after = {key: hash_file(target/key) for key in before}
    result['modified_config_files'] = [key for key in before if before[key] != after[key]]
    audit = target/'config-load.jsonl'
    if audit.is_file():
        result['loads'] = [json.loads(line) for line in audit.read_text().splitlines() if line]
    if MODE == 'init64':
        graph = target/'graph.json'
        result['graph_bytes'] = graph.stat().st_size if graph.is_file() else 0
        result['status'] = 'GRAPH_SAVED' if result['sst_exit'] == 0 and result['graph_bytes'] else 'INIT_FAILED'
    elif result['sst_exit'] == 0 and len(result['completion']) == 1:
        result['phase'] = 'output'
        exit_code, wall, out_log = run_stage('output', ['python3','-B','output_generator.py','v310_config'], 100)
        result['output_exit'], result['output_wall_seconds'] = exit_code, wall
        result['output_files'] = sorted(p.name for p in (target/'bin').glob('david_*_debugRegion.bin'))
        if exit_code != 0:
            result['output_errors'] = selected_logs(out_log, r'Error:|Exception:|error|fail')
        result['status'] = 'SST_COMPLETE' if exit_code == 0 and len(result['output_files']) == 8 else 'OUTPUT_INCOMPLETE'
    else:
        result['status'] = 'SST_FAILED'
except (AssertionError, OSError, ValueError, subprocess.TimeoutExpired) as error:
    result['status'] = 'STOPPED'
    result['reason'] = str(error)[:180] if isinstance(error, AssertionError) else type(error).__name__
if created:
    (target/'bridge-topology-receipt.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
