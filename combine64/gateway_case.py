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

import gateway_fixture

ROOT = Path('/work')
SOURCE = ROOT / 'source'
SUPPORT = SOURCE / 'tools/unitest/simulator/experiments/inter_server_send/sst'
sys.path.insert(0, str(SUPPORT))
WORLD = gateway_fixture.WORLD


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


def run_sst(case, environment, timeout, record, stop_at_us=None, threads=8):
    command = [
        'bash', '-c', 'set -e; source "$1"; shift; exec "$@"',
        'bash', environment['env_script'], 'stdbuf', '-oL', '-eL', environment['sst_bin'],
        '-n', str(threads),
    ]
    if stop_at_us is not None:
        command.append(f'--stop-at={stop_at_us}us')
    command.append(str(case / 'test_UBusSim.py'))
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


def check_outputs(case, environment, record, output_dir='actual'):
    from addressed_exports import reconstruct_die_output, verify_guards

    layout = read_json(case / 'export-layout.json')
    dump_dir = case / 'bin'
    decoder = Path(environment['decoder_library'])
    actual = case / output_dir
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
            address = segment['addr'] - row['sys_va_base_addr']
            if name == 'profile':
                from gateway_profile import reconstruct_profile
                data = reconstruct_profile(paths, address, segment['size'], decoder)
            else:
                data = reconstruct_die_output(paths, address, segment['size'], decoder)
            (out / f'{name}.bin').write_bytes(data)
            fields[name] = fields.get(name, 0) + len(data)
            if name == 'profile':
                continue
            expected = (case / 'fixture-data' / rank_name / f'golden-{name}.bin').read_bytes()
            if data != expected:
                assert len(data) == len(expected), (rank_name, name, len(data), len(expected))
                offset = next(i for i, (got, want) in enumerate(zip(data, expected)) if got != want)
                aligned = offset & ~3
                raise ValueError(f'{rank_name} {name}+{aligned}: got {data[aligned:aligned+8].hex()}, expected {expected[aligned:aligned+8].hex()}')
    record.update(checked_ranks=WORLD, checked_bytes=fields)


def main(config):
    from gateway_profile import summarize

    identity = read_json(ROOT / 'identity.json')
    revision = config['revision']
    assert identity['revision'] == revision
    stage_only = config.get('stage_only', False)
    build_dir = 'gateway-stage-profile-build' if stage_only else 'profile-build'
    build = read_json(ROOT / build_dir / 'build-summary.json')
    obj = build['targets']['fp32-' + gateway_fixture.ENTRY]
    binary = ROOT / obj['path']
    assert build['all_compiled'] and build['sim_send'] and build['profiling'] and digest(binary) == obj['sha256']
    case = ROOT / 'cases' / config['label']
    case.mkdir(parents=True, exist_ok=False)
    cores, chunk, micro = (config[key] for key in ('cores', 'chunk', 'micro'))
    record = dict(case=case.name, candidate_commit=revision, cores=cores, chunk_tokens=chunk,
                  stage='single-core' if micro else 'gateway-reduce', sim_send=True,
                  source_object_sha256=obj['sha256'], profiling_detail='stages' if stage_only else 'tokens')
    started = time.monotonic()
    try:
        stats = gateway_fixture.prepare(SOURCE, case, binary, cores, chunk, micro)
        record.update({key: value for key, value in stats.items() if key != 'rank_stats'})
        record['reduced_tokens'] = sum(row['reduced_tokens'] for row in stats['rank_stats'])
        record['contributions'] = sum(row['contributions'] for row in stats['rank_stats'])
        environment = stage_platform(case, config['platform_sha256'])
        import yaml
        model = case / 'v310_config/davidV100_simulator_config.yml'
        scalar = yaml.safe_load(model.read_text())['simulator_config']['su']
        assert scalar['component'] == 'V310.ScalarUnitL1' and int(scalar['params']['lsu_level']) == 1
        record.update(model_sha256=digest(model), environment_sha256=digest(case / 'sst-environment.json'))
        run_sst(case, environment, config['timeout'], record, threads=config['threads'])
        check_outputs(case, environment, record)
        record.update(summarize(case, stats['rank_stats'], cores, stage_only=stage_only))
        record['status'] = 'sst_and_checker_passed'
    except BaseException as error:
        if record.get('status') not in ('interrupted', 'timed_out'):
            record['status'] = 'failed'
        record['error'] = (type(error).__name__ + ': ' + str(error))[:280]
        traceback.print_exc()
    record['runner_wall_seconds'] = round(time.monotonic() - started, 3)
    write_json(case / 'phase-result.json', record)
    return 0 if record['status'] == 'sst_and_checker_passed' else 1


if __name__ == '__main__':
    raise SystemExit(main(json.loads(sys.argv[1])))
