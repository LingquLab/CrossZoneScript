# Copyright (c) 2026 TileXR Project.
"""Prepare and check cases against the immutable production HT kernels."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import traceback

import fixture

ROOT = Path('/work')
SOURCE = ROOT / 'source'
SUPPORT = SOURCE / 'tools/unitest/simulator/experiments/inter_server_send/sst'
sys.path.insert(0, str(SUPPORT))
WORLD, HIDDEN, TOPK, REDUCERS, FINAL_CORES = 64, 128, 6, 8, 8
STRIPE, MAGIC = 512, 2026
REVISION = '86f802f9f8a19728364c466b0e9cb7416e038595'
LOCAL_ENTRY = 'tilexr_moonep_combine_ht_kernel'
ENTRIES = {
    'local-send': LOCAL_ENTRY,
    'gateway-reduce': LOCAL_ENTRY,
    'local-return': 'tilexr_ep_ht_local_return_kernel',
    'inter-server-send': 'tilexr_ep_ht_inter_server_send_kernel',
    'final-independent': 'tilexr_ep_ht_final_reduce_kernel',
    'joint-producer': 'tilexr_ht_pipeline_sst',
    'final-from-producer': 'tilexr_ep_ht_final_reduce_kernel',
    'joint-final': 'tilexr_ht_pipeline_sst',
}
BLOCKS = {
    'local-send': 8, 'gateway-reduce': 8, 'local-return': 8,
    'inter-server-send': 16, 'final-independent': FINAL_CORES,
    'joint-producer': 24 + REDUCERS, 'final-from-producer': FINAL_CORES,
    'joint-final': 24 + REDUCERS + FINAL_CORES,
}


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, data):
    pending = path.with_suffix(path.suffix + '.tmp')
    pending.write_text(json.dumps(data, indent=2) + '\n')
    pending.replace(path)


def digest(path):
    with path.open('rb') as src:
        sha = hashlib.sha256()
        for chunk in iter(lambda: src.read(1024 * 1024), b''):
            sha.update(chunk)
        return sha.hexdigest()


def object_for(entry, build):
    target = entry + '_kernel' if entry == 'tilexr_ht_pipeline_sst' else entry
    obj = build['targets']['fp32-' + target]
    path = ROOT / obj['path']
    assert obj['exit_code'] == 0 and digest(path) == obj['sha256'], target
    return path


def ensure_asset(tokens, binary):
    asset = ROOT / 'assets' / f't{tokens}'
    if not (asset / 'identity.json').is_file():
        assert not asset.exists(), 'incomplete_fixture_assets'
        asset.parent.mkdir(exist_ok=True)
        try:
            fixture.prepare(SOURCE, asset, WORLD, tokens, binary, disk_backed=True)
        except Exception:
            asset.rename(asset.with_name(asset.name + f'-failed-{time.time_ns()}'))
            raise
    assert read_json(asset / 'identity.json')['candidate_commit'] == REVISION
    return asset


def local_phase(case, asset, action):
    from runtime import BASES

    loader = asset / 'loader'
    case.mkdir(parents=True)
    for name in ('input_bin', 'kernel'):
        shutil.copytree(loader / name, case / name)
    shutil.copytree(asset / 'fixture-data', case / 'fixture-data')
    layout = read_json(loader / 'export-layout.json')
    workload = read_json(loader / 'workload.yml')
    asset_id = read_json(asset / 'identity.json')
    for row in layout['ranks']:
        rank = row['rank']
        rank_name = f'rank{rank:02}'
        inputs = case / 'input_bin' / rank_name
        golden = case / 'fixture-data' / rank_name
        args = bytearray((inputs / 'kernel_args.bin').read_bytes())
        assert len(args) == 248
        struct.pack_into('<II', args, 148, 0, REDUCERS)
        struct.pack_into('<I', args, 156, 1 if action == 'local-send' else 2)
        (inputs / 'kernel_args.bin').write_bytes(args)
        chip = workload['workload_config'][f'chip{rank}']
        chip['block_dim'] = BLOCKS[action]
        for word in (18, 19):
            chip['kernel_param'][f'word{word}']['value'] = struct.unpack_from('<Q', args, word * 8)[0]
        configured_root = asset_id['runtime_root']
        chip['kernel_file'] = chip['kernel_file'].replace(configured_root, str(case))
        for field in chip['kernel_param'].values():
            if 'input_bin' in field:
                field['input_bin'] = field['input_bin'].replace(configured_root, str(case))
        status = b'\xa5' * STRIPE
        if action == 'local-send':
            status = bytes(8 * 4) + status[8 * 4:]
            shutil.copyfile(golden / 'initial-output.bin', golden / 'golden-output.bin')
        else:
            workspace = bytearray((inputs / 'workspace.bin').read_bytes())
            workspace_address = row['sys_va_base_addr'] + BASES['workspace']
            for field in ('returned', 'completed'):
                segment = row['exports'][field]
                offset = segment['addr'] - workspace_address
                payload = (golden / f'golden-{field}.bin').read_bytes()
                assert len(payload) == segment['size']
                workspace[offset:offset + len(payload)] = payload
            (inputs / 'workspace.bin').write_bytes(workspace)
            completed = bytearray((golden / 'golden-completed.bin').read_bytes())
            chunks = asset_id['chunks']
            for reducer in range(REDUCERS):
                struct.pack_into('<I', completed, (chunks * 8 + reducer) * STRIPE, chunks)
            (golden / 'golden-completed.bin').write_bytes(completed)
        (golden / 'golden-status.bin').write_bytes(status)
    write_json(case / 'workload.yml', workload)
    write_json(case / 'export-layout.json', layout)


def stage_platform(case, expected):
    for name, expected_digest in expected.items():
        src = ROOT / 'platform' / name
        assert digest(src) == expected_digest, name
        dst = case / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    shutil.copyfile(case / 'workload.yml', case / 'v310_config/workload_config.yml')
    environment = read_json(case / 'sst-environment.json')
    for key in ('sst_bin', 'env_script', 'decoder_library'):
        assert digest(Path(environment[key])) == environment[key + '_sha256'], key
    for path, expected_digest in environment.get('runtime_files', {}).items():
        assert digest(Path(path)) == expected_digest, path
    return environment


def run_sst(case, environment, timeout, record):
    command = [
        'bash', '-c', 'set -e; source "$1"; shift; exec "$@"',
        'bash', environment['env_script'], environment['sst_bin'],
        '-n', '8', str(case / 'test_UBusSim.py'),
    ]
    start = time.monotonic()
    record.update(status='running', command=command, started_unix=time.time())
    with (case / 'sim.log').open('wb') as log:
        proc = subprocess.Popen(command, cwd=case, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        def interrupt(signum, frame):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, interrupt)
        record['pid'] = proc.pid
        write_json(case / 'run.json', record)
        try:
            proc.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            record['status'] = 'timed_out' if isinstance(error, subprocess.TimeoutExpired) else 'interrupted'
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
            raise
        finally:
            signal.signal(signal.SIGTERM, previous)
            record.update(sst_wall_seconds=round(time.monotonic() - start, 3), returncode=proc.returncode)
            write_json(case / 'run.json', record)
    with (case / 'sim.log').open('rb') as log:
        log.seek(max(0, log.seek(0, os.SEEK_END) - 131072))
        tail = log.read().decode(errors='replace')
    match = re.search(r'Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)', tail)
    if proc.returncode or not match or float(match[1]) <= 0:
        raise ValueError('SST did not complete normally, exit=' + str(proc.returncode))
    record['platform_simulated_us'] = float(match[1]) * {'ns': .001, 'us': 1, 'ms': 1000, 's': 1e6}[match[2]]


def check_outputs(case, environment, record):
    from addressed_exports import reconstruct_die_output, verify_guards

    layout = read_json(case / 'export-layout.json')
    dump_dir = case / 'bin'
    decoder = Path(environment['decoder_library'])
    actual = case / 'actual'
    actual.mkdir()
    assert len(layout['ranks']) == WORLD
    record['guards'] = verify_guards(layout, dump_dir, decoder)['count']
    fields = {}
    for row in layout['ranks']:
        rank = row['rank']
        rank_name = f'rank{rank:02}'
        out = actual / rank_name
        out.mkdir()
        for name, segment in row['exports'].items():
            paths = [dump_dir / f'david_{rank}_die{die}_{name}.bin' for die in (1, 2)]
            data = reconstruct_die_output(paths, segment['addr'] - row['sys_va_base_addr'], segment['size'], decoder)
            (out / f'{name}.bin').write_bytes(data)
            expected = (case / 'fixture-data' / rank_name / f'golden-{name}.bin').read_bytes()
            if data != expected:
                assert len(data) == len(expected), (rank_name, name, len(data), len(expected))
                offset = next(i for i, (got, want) in enumerate(zip(data, expected)) if got != want)
                aligned = offset & ~3
                raise ValueError(f'{rank_name} {name}+{aligned}: got {data[aligned:aligned+8].hex()}, expected {expected[aligned:aligned+8].hex()}')
            fields[name] = fields.get(name, 0) + len(data)
    record.update(checked_ranks=WORLD, checked_bytes=fields)


def main(config):
    action, tokens, attempt, timeout = (config[key] for key in ('action', 'tokens', 'attempt', 'timeout'))
    assert read_json(ROOT / 'identity.json')['revision'] == REVISION
    build = read_json(ROOT / 'build-summary.json')
    assert build['all_compiled'] and build['sim_send']
    case = ROOT / 'cases' / f't{tokens}-{action}-a{attempt}'
    assert not case.exists(), 'case_already_exists'
    record = {'candidate_commit': REVISION, 'stage': action, 'tokens': tokens, 'attempt': attempt,
              'world': WORLD, 'hidden': HIDDEN, 'topk': TOPK, 'dtype': 'fp32', 'sim_send': True,
              'blocks': BLOCKS[action], 'gateway_reducers': REDUCERS, 'final_cores': FINAL_CORES}
    started = time.monotonic()
    write_json(ROOT / 'progress.json', {'stage': 'prepare_case', 'case': case.name, 'updated_unix': time.time()})
    try:
        asset = ensure_asset(tokens, object_for(LOCAL_ENTRY, build))
        record['routes_sha256'] = digest(asset / 'routes.json')
        if action in ('local-send', 'gateway-reduce'):
            local_phase(case, asset, action)
        else:
            from return_case import prepare_return_case
            record.update(prepare_return_case(case, asset, action, object_for(ENTRIES[action], build), config.get('source_case')))
        record['kernel_sha256'] = digest(case / 'kernel/inter_server_send.o')
        record['source_object_sha256'] = digest(object_for(ENTRIES[action], build))
        env = stage_platform(case, config['platform_sha256'])
        record['environment_sha256'] = digest(case / 'sst-environment.json')
        write_json(ROOT / 'progress.json', {'stage': 'sst', 'case': case.name, 'updated_unix': time.time()})
        run_sst(case, env, timeout, record)
        check_outputs(case, env, record)
        record['status'] = 'sst_and_checker_passed'
    except BaseException as error:
        if record.get('status') not in ('timed_out', 'interrupted'):
            record['status'] = 'failed'
        record['error'] = (type(error).__name__ + ': ' + str(error))[:320]
        traceback.print_exc()
    record['runner_wall_seconds'] = round(time.monotonic() - started, 3)
    case.mkdir(parents=True, exist_ok=True)
    write_json(case / 'phase-result.json', record)
    if (case / 'run.json').is_file():
        write_json(case / 'run.json', record)
    write_json(ROOT / 'progress.json', {'stage': record['status'], 'case': case.name, 'updated_unix': time.time()})
    return 0 if record['status'] == 'sst_and_checker_passed' else 1


if __name__ == '__main__':
    raise SystemExit(main(json.loads(sys.argv[1])))
