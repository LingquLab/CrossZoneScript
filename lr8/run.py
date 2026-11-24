"""Package pinned TileXR and SST sources, then run the requested cases over SSH."""
import argparse
import base64
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

REPO = 'D:/workspace/TileXR'
TOOL = '96581a23de5e7e87ce90c770fa130a596600944e'
GENERATOR = '23a542b2e6bfcc34f85313d02677a4aafdfc3efb'
HERE = Path(__file__).resolve().parent

def git(*args):
    return subprocess.check_output(['git', '-C', REPO, *args], stderr=subprocess.PIPE, timeout=120)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    revision = args['revision']
    assert re.fullmatch('[0-9a-f]{40}', revision)
    assert re.fullmatch('[a-z0-9-]{1,45}', args['label'])
    assert args['cases'] and len(args['cases']) <= 16
    for ranks, elements in args['cases']:
        assert ranks in (8, 16, 32, 64)
        assert elements in (128, 512 * 1024 // (ranks * 4), 4 * 1024**2 // (ranks * 4), 16 * 1024**2 // (ranks * 4))
    settings = args.get('schedules', [0])
    assert all(type(value) is int and 0 <= value <= 0xFFFFFFFF for value in settings)
    assert 1 <= len(settings) <= 12 and len(settings) == len(set(settings))
    if args.get('tune'):
        assert args.get('profiling') and 'schedules' not in args
    if 'clock_seed' in args:
        assert args.get('tune') and len(args['cases']) == 1 and args['cases'][0][0] >= 32
        assert type(args['clock_seed']) is int and 0 < args['clock_seed'] < 0xffffffff
    if args.get('instruction_dump_chips'):
        assert len(args['cases']) == 1
        chips = args['instruction_dump_chips']
        assert len(chips) == len(set(chips)) and all(type(chip) is int and 0 <= chip < args['cases'][0][0] for chip in chips)
    assert args.get('routing', 'installed') in ('installed', 'with-clos')
    assert args.get('sim_variant', 'sim-split1') in ('sim-split1', 'sim-device-split1')
    name = args.get('kernel_name', 'urma_lr8')
    assert name in ('urma_lr8', 'urma_cw', 'urma_lr8_p16_s16_local7', 'urma_lr8_p16_s8_local7')
    if name != 'urma_lr8':
        assert settings == [0] and not args.get('profiling') and not args.get('tune')
    assert args.get('optimization', 'O2') in ('O1', 'O2')
    assert type(args.get('ta_tp_info', False)) is bool
    assert type(args.get('zero_logging', False)) is bool
    assert not (args.get('zero_logging') and args.get('ta_tp_info'))
    if 'ubus_replacement' in args:
        assert args['ubus_replacement'] == '/home/l00654177/libUBusSimulator.so'
    if 'ubus_config_override' in args:
        assert args.get('zero_logging')
        assert args['ubus_config_override'] == (
            '/opt/tilexr-lr8-0c04d831-32p-nocredit-ta-tp-retry-4lxg_qc8/'
            '32p-131072i32-0-measurement/UBusSim_config/UBusSim_simulator_config.yml')
    if 'threads' in args:
        threads = args['threads']
        assert type(threads) is int and 1 <= threads <= min(32, *(ranks for ranks, _ in args['cases']))
    for commit in (revision, TOOL, GENERATOR):
        git('fetch', '--no-tags', 'origin', commit)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        paths = ('tools/simulator', 'tools/tssh')
        archive = git('archive', TOOL, *paths)
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            stream.extractall(root, filter='data')
        sys.path.insert(0, str(root / 'tools/simulator'))
        tool_dir = root / 'tools/simulator'
        if b'include/tilexr/comm/args.h' in git('ls-tree', '-r', '--name-only', revision, 'include').splitlines():
            packager = tool_dir / 'sources.py'
            source = packager.read_text(encoding='utf-8')
            old = "['tools/profiling'] if prefix == 'runtime/'"
            assert source.count(old) == 1
            source = source.replace(old, "['include', 'tools/profiling'] if prefix == 'runtime/'")
            anchor = '    if not files or len(files) > 4096'
            assert source.count(anchor) == 1
            adapter = """    path = 'tooling/tools/unitest/simulator/experiments/alltoall_udma/runtime.py'
    old_layout = b'COMM_BYTES = 530024'
    if files[path].count(old_layout) != 1:
        raise ValueError('Unexpected frozen CommArgs fixture')
    files[path] = files[path].replace(old_layout, b'COMM_BYTES = 529944')
"""
            packager.write_text(source.replace(anchor, adapter + anchor), encoding='utf-8')
        from sources import package
        payload = root / 'sources.tar.gz'
        lock = package(REPO, revision, GENERATOR, payload)
        sources = (payload.read_bytes(), (tool_dir / 'kernel_worker.py').read_bytes(),
                   (tool_dir / 'suite.py').read_bytes())
        data = [base64.b64encode(source).decode('ascii') for source in sources]
    program = 'ARGS = ' + repr(args) + '\nLOCK = ' + repr(lock) + '\nDATA = ' + repr(data) + '\n'
    program += (HERE / 'remote.py').read_text(encoding='utf-8')
    result = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'IdentitiesOnly=yes',
         '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
         '-i', 'C:/Users/l00929943/.ssh/id_ed25519_server', 'root@141.61.94.30', 'python3 -'],
        input=program.encode('utf-8'), capture_output=True, timeout=args.get('batch_timeout', 1500))
    lines = result.stdout.decode('utf-8', 'replace').splitlines()
    summaries = [json.loads(line) for line in lines if line.startswith('{')]
    print(json.dumps(dict(ssh_exit=result.returncode, records=summaries,
                          stderr_bytes=len(result.stderr))))
    return result.returncode

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(stage='transport_or_staging', error_class=type(error).__name__)))
        sys.exit(1)
