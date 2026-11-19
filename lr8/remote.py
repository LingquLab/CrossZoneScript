"""Run pinned TileXR SST workers in an exclusive container, preserving stage logs."""
import base64
import hashlib
import json
from pathlib import Path
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

def run():
    global STAGE, STARTED, ROOT
    actual = docker('image', 'inspect', '--format', '{{.Id}}', IMAGE).decode().strip()
    assert actual == IMAGE_ID
    ROOT = Path(tempfile.mkdtemp(prefix=NAME + '-', dir='/opt'))
    cid = docker('run', '--pull=never', '-dt', '--name', NAME, '--runtime=ascend',
                 '--ipc=shareable', '--shm-size=64m', '--ulimit', 'nofile=524288:524288',
                 '--mount', 'type=bind,src=' + str(ROOT) + ',dst=/work', IMAGE, '/bin/bash', '-l').decode().strip()
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
    (ROOT / 'kernel_worker.py').write_bytes(worker)
    suite = base64.b64decode(DATA[2], validate=True).decode('utf-8').replace('\r\n', '\n')
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
        sim_variant='sim-split1', stage_profiling=0,
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
    manifest = json.loads((ROOT / 'kernel/sim-split1/build_manifest.json').read_text())
    print(json.dumps(dict(runtime_commit=LOCK['runtime_commit'], generator_commit=LOCK['tooling_commit'],
                          binary_sha256=manifest['binary_sha256'], transport=manifest['features'])), flush=True)
    for ranks, elements in ARGS['cases']:
        label = f'{ranks}p-{elements}i32'
        sim_timeout = ARGS.get('simulation_timeout', 600)
        options = dict(environment_report='/work/environment', suite_output='/work/' + label,
                       rank_size=ranks, elements_per_peer=elements, threads=min(ranks, 32),
                       simulation_timeout=sim_timeout)
        phase('prepare', options, 250, 'prepare-' + label)
        phase('run', options, sim_timeout + 240, 'run-' + label)
        result = json.loads((ROOT / label / 'execution.json').read_text())
        print(json.dumps(dict(ranks=ranks, elements_per_peer=elements, verified=True,
                              simulated_us=result['simulated_us'])), flush=True)

try:
    run()
except Exception as error:
    print(json.dumps(dict(stage=STAGE, error_class=type(error).__name__,
                          returncode=getattr(error, 'returncode', None))), flush=True)
    raise SystemExit(1)
finally:
    if STARTED:
        stopped = subprocess.run(['docker', 'stop', '--time', '15', NAME],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
        print(json.dumps(dict(container_stopped=stopped.returncode == 0)), flush=True)
