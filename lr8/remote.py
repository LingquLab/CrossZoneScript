"""Run pinned TileXR SST workers in an exclusive container, preserving stage logs."""
import base64
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile

IMAGE = 'tilexr-sim-9.1:aarch64-ac2cee81-v1.0.0'
IMAGE_ID = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
NAME = 'tilexr-lr8-0c04d831-' + ARGS['label']
STAGE = 'container'
STARTED = False
ROOT = None

def docker(*args):
    return subprocess.check_output(['docker', *args], stderr=subprocess.PIPE, timeout=50)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def phase_boundaries(destination, ranks):
    groups = (ranks + 14) // 16
    starts = [[] for _ in range(groups)]
    completions = [[] for _ in range(groups)]
    for rank in range(ranks):
        data = (destination / f'rank{rank}.records.bin').read_bytes()
        offset = 48
        while offset < len(data):
            stream, count, _, _, size = struct.unpack_from('<IIIIQ', data, offset)
            core = stream & 0xffff
            samples = list(struct.iter_unpack('<QII', data[offset + 24:offset + size]))
            assert count == len(samples)
            offset += size
            if core >= 32:
                continue
            lane, route = core % 16, core // 16
            active = []
            for group in range(groups):
                distance = group * 8 + lane % 8 + 1
                if distance > ranks // 2 or (lane >= 8 and distance == ranks // 2):
                    continue
                peer = (rank + (distance if lane < 8 else -distance)) % ranks
                if route == 0 or rank // 8 != peer // 8:
                    active.append(group)
            posted = [tick for tick, event, _ in samples if event == 10]
            quiet = [tick for tick, event, _ in samples if event == 17]
            assert len(active) == len(posted) == len(quiet)
            for group, begin, end in zip(active, posted, quiet):
                starts[group].append(begin)
                completions[group].append(end)
    first = [min(group) for group in starts]
    last = [max(group) for group in completions]
    return dict(first_send_cycle=first, last_quiet_cycle=last,
                gap_cycles=[begin - end for begin, end in zip(first[1:], last)])

def select_period(measure, seed):
    """Bracket a valid period, refine the boundary, then rank valid samples by SST time."""
    trials = {}
    low = 0
    high = None
    period = max(1, seed)
    for _ in range(7):
        row = measure(period, 'search')
        trials[period] = row
        if min(row['gap_cycles']) >= 0:
            high = period
        else:
            low = period
        if high is None:
            period = min(0xfffffffe, period + max(64, period // 8, -min(row['gap_cycles'])))
        else:
            period = (low + high) // 2
        if period == 0 or period in trials:
            break
    valid = [trial for trial in trials.values() if min(trial['gap_cycles']) >= 0]
    if not valid:
        raise RuntimeError('No valid LR8 clock period in the measured search')
    return min(valid, key=lambda row: (row['simulated_us'], row['schedule']))['schedule']

def run():
    global STAGE, STARTED, ROOT
    actual = docker('image', 'inspect', '--format', '{{.Id}}', IMAGE).decode().strip()
    assert actual == IMAGE_ID
    ROOT = Path(tempfile.mkdtemp(prefix=NAME + '-', dir='/opt'))
    cid = docker('run', '-dt', '--name', NAME, '--runtime=ascend',
                 '--ipc=shareable', '--shm-size=4g',
                 '-v', str(ROOT) + ':/work', IMAGE_ID, '/bin/bash', '-l').decode().strip()
    STARTED = True
    assert docker('inspect', '--format', '{{.Id}} {{.Image}}', NAME).decode().split() == [cid, IMAGE_ID]
    print(json.dumps(dict(container=NAME, container_id=cid, root=str(ROOT), image_id=IMAGE_ID)), flush=True)
    STAGE = 'source_lock'
    archive = ROOT / 'sources.tar.gz'
    archive.write_bytes(base64.b64decode(DATA[0], validate=True))
    assert digest(archive) == LOCK['archive_sha256']
    sources = ROOT / 'sources'
    sources.mkdir()
    with tarfile.open(archive) as stream:
        stream.extractall(sources, filter='data')
    assert digest(sources / 'SOURCE_LOCK.json') == LOCK['source_lock_sha256']
    worker = base64.b64decode(DATA[1], validate=True)
    if (sources / 'runtime/include/tilexr/comm/args.h').is_file():
        worker = worker.replace(b'root / "include", root / "collectives/kernels"',
                                b'root.parent / "include", root / "collectives/kernels"')
        worker = worker.replace(b'str(headers / "include")', b'str(headers.parent / "include")')
        old_probe = b'        command = [compiler, "-std=c++14",'
        new_probe = b"""        probe_source = output / "abi_probe.cpp"
        frozen = (tooling / "abi_probe.cpp").read_text(encoding="utf-8")
        probe_source.write_text(frozen.replace('"tilexr_udma_types.h"', '"tilexr/comm/udma/types.h"')
                                .replace('"tilexr_udma_reg.h"', '"tilexr/comm/udma/registry.h"'), encoding="utf-8")
        command = [compiler, "-std=c++14","""
        assert worker.count(old_probe) == 1
        worker = worker.replace(old_probe, new_probe).replace(b'str(tooling / "abi_probe.cpp")', b'str(probe_source)')
    optimization = ARGS.get('optimization', 'O2')
    if optimization == 'O1':
        anchor = b'options = ["-xcce", "-O2",'
        assert worker.count(anchor) == 1
        worker = worker.replace(anchor, b'options = ["-xcce", "-O1",')
    if ARGS.get('cq_gm_doorbell'):
        anchor = b'SPLIT_UDMA_WRITES=splitting, CQ_GM_DOORBELL=0)'
        assert worker.count(anchor) == 1
        worker = worker.replace(anchor, b'SPLIT_UDMA_WRITES=splitting, CQ_GM_DOORBELL=int(label == variant))')
    (ROOT / 'kernel_worker.py').write_bytes(worker)
    suite = base64.b64decode(DATA[2], validate=True).decode('utf-8').replace('\r\n', '\n')
    if ARGS.get('cq_gm_doorbell'):
        anchor = 'cq_store_instruction="st_dev", cq_address_contract='
        assert suite.count(anchor) == 1
        suite = suite.replace(anchor, 'cq_store_instruction="mte3", cq_address_contract=')
    if ARGS.get('profiling', False):
        anchor = 'PROFILE_STREAMS, PROFILE_CAPACITY = 33, 256'
        assert suite.count(anchor) == 1
        suite = suite.replace(anchor, 'PROFILE_STREAMS, PROFILE_CAPACITY = 33, 512')
    if any(ranks * ranks * elements * 4 > 128 * 1024**2 for ranks, elements in ARGS['cases']):
        with open('/proc/meminfo', encoding='ascii') as info:
            available_kib = next(int(line.split()[1]) for line in info if line.startswith('MemAvailable:'))
        assert available_kib >= 32 * 1024**2
        assert suite.count('1024 ** 3') == 2
        suite = suite.replace('1024 ** 3', '8 * 1024 ** 3')
    if ARGS.get('lsu_level') == 2:
        anchor = '    configs, lsu_level = configure(resources, args)'
        assert suite.count(anchor) == 1
        suite = suite.replace(anchor, anchor + '''
    su = configs[V310_CONFIG]['simulator_config']['su']
    assert lsu_level == 1 and su['params']['lsu_level'] == 1
    lsu_level = su['params']['lsu_level'] = 2''')
    if ARGS.get('ta_tp_info'):
        anchor = '    configs, lsu_level = configure(resources, args)'
        assert suite.count(anchor) == 1
        suite = suite.replace(anchor, anchor + '''
    def logging_levels(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ('log_level', 'verbose', 'verbosity'):
                    yield value
                else:
                    yield from logging_levels(value)
        elif isinstance(node, list):
            for value in node:
                yield from logging_levels(value)

    before = [level for doc in configs.values() for level in logging_levels(doc)]
    require(before and set(before) == {0}, 'Expected baseline logging levels of zero')
    ubus = configs['UBusSim_config/UBusSim_simulator_config.yml']['simulator_config']
    for component in ('ta_layer', 'tp_layer'):
        params = ubus[component]['params']
        require(params['verbose'] == 0, 'Expected component verbosity of zero')
        params['verbose'] = 2
    after = [level for doc in configs.values() for level in logging_levels(doc)]
    require(after.count(2) == 2 and after.count(0) == len(before) - 2,
            'Expected only TA and TP at verbosity 2')''')
    if ARGS.get('tune') or any(ARGS.get('schedules', [])):
        anchor = '    from case_tools import preflight\n\n    output = Path(config["suite_output"])'
        assert suite.count(anchor) == 1
        adapter = '''
    parameter_values = rt.parameters
    def scheduled_parameters(*args):
        values = parameter_values(*args)
        values['reservedProfiling'] = config['lr8_schedule']
        return values
    rt.parameters = scheduled_parameters
'''
        suite = suite.replace(anchor, '    from case_tools import preflight\n' + adapter
                              + '\n    output = Path(config["suite_output"])')
    (ROOT / 'suite.py').write_text(suite, encoding='utf-8')
    (ROOT / 'worker.py').write_text(
        "import sys, types\n"
        "_module = types.ModuleType('tilexr_current_suite')\n"
        "sys.modules[_module.__name__] = _module\n"
        "_module.SOURCE_TEXT = open('/work/suite.py', encoding='utf-8').read()\n"
        "exec(compile(_module.SOURCE_TEXT, '/work/suite.py', 'exec'), _module.__dict__)\n"
        "import json\n"
        "from kernel_worker import run\n"
        "run(json.loads(sys.argv[1]))\n", encoding='utf-8')
    cann_env = '/opt/sim/cann-env.sh'
    ubus = '/opt/sim/plugins/libUBusSimulator.so'
    STAGE = 'environment_files'
    hashes = docker('exec', NAME, 'sha256sum', cann_env, ubus).decode().splitlines()
    config = dict(
        sources='/work/sources', output='/work/kernel', runtime_commit=LOCK['runtime_commit'],
        source_lock_sha256=LOCK['source_lock_sha256'], cann_root='/opt/sim/Ascend/cann-9.1.0',
        driver_root='/usr/local/Ascend/driver', cann_env=cann_env, cann_env_sha256=hashes[0].split()[0],
        ubus_library=ubus, ubus_sha256=hashes[1].split()[0], kernel_name='urma_lr8',
        sim_variant=ARGS.get('sim_variant', 'sim-split1'),
        stage_profiling=int(ARGS.get('profiling', False)),
        suite_adapter_sha256=hashlib.sha256(suite.encode()).hexdigest())

    def phase(mode, options, timeout, log_name):
        global STAGE
        STAGE = log_name
        command = ['python3', '-B', '-u', '/work/worker.py', json.dumps(dict(config, mode=mode, **options))]
        if mode == 'build':
            command = ['bash', '-c', 'set -e; source "$1"; shift; exec "$@"', 'bash', cann_env, *command]
        with (ROOT / (log_name + '.log')).open('xb') as log:
            subprocess.run(['docker', 'exec', NAME, *command], stdout=log, stderr=subprocess.STDOUT,
                           timeout=timeout, check=True)

    phase('build', {}, 360, 'build')
    phase('abi', {}, 150, 'abi')
    phase('inspect', dict(library_dir='/opt/sim/plugins', inspection_output='/work/environment'), 150, 'inspect')
    manifest = json.loads((ROOT / 'kernel' / config['sim_variant'] / 'build_manifest.json').read_text())
    print(json.dumps(dict(runtime_commit=LOCK['runtime_commit'], generator_commit=LOCK['tooling_commit'],
                          binary_sha256=manifest['binary_sha256'], transport=manifest['features'],
                          optimization=optimization)), flush=True)
    for ranks, elements in ARGS['cases']:
        def measure(setting, sample):
            label = f'{ranks}p-{elements}i32-{setting}-{sample}'
            sim_timeout = ARGS.get('simulation_timeout', 600)
            options = dict(environment_report='/work/environment', suite_output='/work/' + label,
                           rank_size=ranks, elements_per_peer=elements,
                           threads=ARGS.get('threads', min(ranks, 32)),
                           simulation_timeout=sim_timeout, lr8_schedule=setting)
            phase('prepare', dict(options, fullmesh_routing=ARGS.get('routing', 'installed'),
                                  instruction_dump_chips=ARGS.get('instruction_dump_chips', [])),
                  600, 'prepare-' + label)
            phase('run', options, sim_timeout + 240, 'run-' + label)
            case = ROOT / label
            result = json.loads((case / 'execution.json').read_text())
            row = dict(ranks=ranks, elements_per_peer=elements, schedule=setting, sample=sample,
                       payload_verified=True, simulated_us=result['simulated_us'],
                       routing=ARGS.get('routing', 'installed'))
            scenario = json.loads((case / 'scenario.json').read_text())
            row['lsu_level'] = scenario['simulator']['lsu_level']
            if ARGS.get('ta_tp_info'):
                log, = case.glob('runs/*/sim.log')
                row['sim_log'] = str(log)
                row['sim_log_bytes'] = log.stat().st_size
            if config['stage_profiling']:
                row.update(phase_boundaries(case / 'stage-profiling', ranks))
            print(json.dumps(row), flush=True)
            return row

        if not ARGS.get('tune'):
            for setting in ARGS.get('schedules', [0]):
                measure(setting, 'measurement')
            continue

        seed = ARGS.get('clock_seed')
        if seed is None:
            baseline = measure(0, 'measurement')
            measure(0xffffffff, 'measurement')
            prior_groups = baseline['last_quiet_cycle'][:-1]
            if not prior_groups:
                measure(1, 'measurement')
                continue
            seed = max((end + index - 1) // index for index, end in enumerate(prior_groups, 1))
        period = select_period(measure, seed)
        final = measure(period, 'confirmation')
        if min(final['gap_cycles']) < 0:
            raise RuntimeError('Selected LR8 clock period overlapped in confirmation')

try:
    run()
except Exception as error:
    if ROOT is not None and isinstance(error, subprocess.CalledProcessError) and error.stderr:
        (ROOT / (STAGE + '-stderr.log')).write_bytes(error.stderr)
    print(json.dumps(dict(stage=STAGE, error_class=type(error).__name__,
                          returncode=getattr(error, 'returncode', None))), flush=True)
    raise SystemExit(1)
finally:
    if STARTED:
        stopped = subprocess.run(['docker', 'stop', '--time', '15', NAME],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
        print(json.dumps(dict(container_stopped=stopped.returncode == 0)), flush=True)
