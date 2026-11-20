from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time


TESTCASES = Path('/opt/sim/simulator/Testcases')
SOURCE = TESTCASES / 'dispatch_memory_64p_top6_profile_bridge_20261120_native_clean01'
PREFIX = 'dispatch_memory_64p_top6_profile_bridge_20261120_sst_tune01'
PACKAGE = Path('/work/src/dispatch_memory_64p_handoff')
KERNEL = 'tilexr_ep_dispatch_memory_kernel.o'
KERNEL_SHA = '7bcb77bf4be8f61cbc8172180e8ef877de40010ef3070943a5f88bd6cf09f625'
WORKLOADS = ('workload.yml', 'workload_config.yml', 'v310_config/workload_config.yml')
STOP_TIME_US = 30.0
VERSION = '14.0.0'
LAUNCH = (
    'source /opt/sim/sst-env.sh && '
    'export SST_PATH=/opt/sim/local/sstcore '
    'SST_CONFIG_FILE_PATH=/opt/sim/simulator/config/sst-plugins.conf '
    'SST_CONFIG_FILE_PATH_SEPARATOR=: && ulimit -n 524288 && '
    'exec timeout -k 15s "$1" taskset -c "$2" sst -n "$3" --print-timing-info '
    '"${@:4}" ./audited_launcher.py'
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def check_idle():
    for comm in Path('/proc').glob('[0-9]*/comm'):
        try:
            name = comm.read_text().strip()
            state = (comm.parent / 'stat').read_text().rsplit(')', 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            continue
        assert name not in ('sst', 'sstsim.x', 'sstsim') or state in ('Z', 'X'), 'SST_BUSY'


def select_affinity():
    allowed = os.sched_getaffinity(0)
    rows = subprocess.check_output(['lscpu', '-p=CPU,CORE,SOCKET,NODE'], text=True)
    physical = {}
    for row in rows.splitlines():
        if row.startswith('#'):
            continue
        cpu, core, socket, node = map(int, row.split(','))
        if cpu in allowed:
            physical.setdefault(node, {}).setdefault((socket, core), cpu)
    node, cpus = min((node, sorted(cores.values())[:40])
                     for node, cores in physical.items() if len(cores) >= 40)
    return node, ','.join(map(str, cpus))


def save(state):
    temporary = RECEIPT.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2) + '\n')
    temporary.replace(RECEIPT)


def stage_case(case, source_manifest):
    case.mkdir()
    for directory in ('input', 'expected', 'UBusSim_config', 'v310_config'):
        shutil.copytree(SOURCE / directory, case / directory)
    for name in (KERNEL, 'test_UBusSim.py', 'audited_launcher.py',
                 'output_generator.py', 'expected-v310-runtime.json'):
        shutil.copy2(SOURCE / name, case / name)
    old_workload = (SOURCE / WORKLOADS[0]).read_bytes()
    assert sha(old_workload) == source_manifest['workload_sha256'], 'SOURCE_WORKLOAD_SHA'
    new_workload = old_workload.replace(str(SOURCE).encode(), str(case).encode())
    assert new_workload != old_workload, 'NO_WORKLOAD_PATHS_REBASED'
    for name in WORKLOADS:
        assert (SOURCE / name).read_bytes() == old_workload, 'SOURCE_WORKLOAD_CONFLICT'
        (case / name).write_bytes(new_workload)
    manifest = dict(source_manifest, kernel_file=str(case / KERNEL), workload_sha256=sha(new_workload))
    (case / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for directory in ('bin', 'OUTPUT', 'dumps'):
        (case / directory).mkdir()
    log_path = case / 'preflight.log'
    with log_path.open('xb') as log:
        preflight = subprocess.run(
            ['python3', '-B', str(PACKAGE / 'generator/preflight_64p.py'), '--testcase', str(case)],
            cwd=case, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    assert preflight.returncode == 0, 'PREFLIGHT_FAILED'
    check = json.loads(log_path.read_text())
    assert (check['status'], check['world_size'], check['blocks_per_rank'],
            check['input_golden_members'], check['profile_dump_regions'],
            check['kernel_sha256']) == ('PASS', 64, 64, 448, 128, KERNEL_SHA), 'PREFLIGHT_IDENTITY'


def simulate(case, source_manifest, workers, stop_us):
    entry = {'case': str(case), 'threads': workers, 'stop_at_us': stop_us, 'phase': 'stage'}
    state['runs'].append(entry)
    save(state)
    stage_case(case, source_manifest)
    check_idle()
    memory_line = next(line for line in Path('/proc/meminfo').read_text().splitlines()
                       if line.startswith('MemAvailable:'))
    entry['host_mem_available_kib'] = int(memory_line.split()[1])
    entry['phase'] = 'run'
    save(state)
    limit = 720 if stop_us else 3600
    argv = ['bash', '-lc', LAUNCH, 'launch', str(limit), state['cpus'], str(workers)]
    if stop_us:
        argv.append(f'--stop-at={stop_us:g}us')
    log_path = case / 'run.log'
    start = time.monotonic()
    with log_path.open('xb') as log:
        result = subprocess.run(argv, cwd=case, stdout=log, stderr=subprocess.STDOUT, timeout=limit + 35)
    entry['sst_wall_seconds'] = round(time.monotonic() - start, 3)
    entry['run_exit_code'] = result.returncode
    finishes = []
    errors = set()
    with log_path.open(errors='replace') as log:
        for line in log:
            match = re.search(r'Simulation is complete, simulated time:\s*([0-9.]+)\s*(ps|ns|us|ms|s)', line)
            if match:
                value, unit = match.groups()
                finishes.append(float(value) * {'ps': 1e-6, 'ns': 1e-3, 'us': 1, 'ms': 1e3, 's': 1e6}[unit])
            errors.update(kind for kind in ('FATAL', 'Traceback', 'Unknown low level', 'dumped core') if kind in line)
    entry['completion_us'] = finishes
    entry['errors'] = sorted(errors)
    save(state)
    assert result.returncode == 0 and len(finishes) == 1 and not errors, 'SST_INCOMPLETE'
    if stop_us:
        assert abs(finishes[0] - stop_us) < 0.01, 'STOP_TIME_MISMATCH'
    entry['phase'] = 'done'
    save(state)
    return entry


def report(case, entry):
    entry['phase'] = 'output'
    save(state)
    start = time.monotonic()
    with (case / 'output-generator.log').open('xb') as log:
        result = subprocess.run(
            ['python3', '-B', 'output_generator.py', 'v310_config'],
            cwd=case, stdout=log, stderr=subprocess.STDOUT, timeout=300)
    entry['output_generator_wall_seconds'] = round(time.monotonic() - start, 3)
    entry['output-generator_exit_code'] = result.returncode
    save(state)
    assert result.returncode == 0, 'OUTPUT_GENERATOR_FAILED'
    launch_receipt = dict(entry, status='SST_COMPLETE_PENDING_BUSINESS_AND_PROFILE_VALIDATION')
    (case / 'bridge-run-receipt.json').write_text(json.dumps(launch_receipt) + '\n')
    entry['phase'] = 'analysis'
    save(state)
    report_script = report_source.replace(
        b'dispatch_memory_64p_top6_profile_bridge_20261119_run07_corecount', case.name.encode())
    completed = subprocess.run(
        ['python3', '-B', '-'], input=report_script, capture_output=True, timeout=650)
    (case / 'analysis-driver.log').write_bytes(completed.stderr)
    (case / 'analysis-driver.json').write_bytes(completed.stdout)
    assert completed.returncode == 0, 'ANALYSIS_DRIVER_FAILED'
    state['analysis'] = json.loads(completed.stdout)
    assert state['analysis']['status'] == 'PASS', 'BUSINESS_OR_PROFILE_ANALYSIS_FAILED'
    entry['phase'] = 'done'
    save(state)


RECEIPT = TESTCASES / (PREFIX + '.json')
state = {'status': 'STARTING', 'sst_version': VERSION, 'source_case': str(SOURCE), 'runs': []}
try:
    with RECEIPT.open('x') as stream:
        stream.write(json.dumps(state) + '\n')
    check_idle()
    state['numa_node'], state['cpus'] = select_affinity()
    source_manifest = json.loads((SOURCE / 'manifest.json').read_text())
    assert source_manifest['kernel_sha256'] == KERNEL_SHA and sha((SOURCE / KERNEL).read_bytes()) == KERNEL_SHA
    assert not (SOURCE / 'v310_config/pv_simulator_config.yml').exists(), 'CONFLICTING_PV_CONFIG'
    assert shutil.disk_usage(TESTCASES).free >= 10 * 1024**3, 'INSUFFICIENT_DISK'
    state['status'] = 'RUNNING'
    short = [simulate(TESTCASES / f'{PREFIX}_short{n}', source_manifest, n, STOP_TIME_US) for n in (20, 40)]
    winner = min(short, key=lambda entry: entry['sst_wall_seconds'])
    state['selected_threads'] = winner['threads']
    full_case = TESTCASES / (PREFIX + '_full')
    result = simulate(full_case, source_manifest, winner['threads'], None)
    report(full_case, result)
    state['status'] = 'PASS'
    save(state)
except FileExistsError:
    state['status'] = 'EXISTING_TASK_EFFECTS'
except (AssertionError, KeyError, OSError, ValueError, subprocess.TimeoutExpired) as error:
    state['status'] = 'STOPPED'
    state['error_kind'] = type(error).__name__
    if isinstance(error, AssertionError):
        state['reason'] = str(error)
    save(state)
print(json.dumps(state, ensure_ascii=True, separators=(',', ':')))
