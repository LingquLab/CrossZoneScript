# Copyright (c) 2026 TileXR Project.
"""Prepare and check cases against the immutable production HT kernels."""
import hashlib
import json
import os
from pathlib import Path
import re
import resource
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
    'joint-reuse-final': 'tilexr_ht_pipeline_sst',
}
BLOCKS = {
    'local-send': 8, 'gateway-reduce': 8, 'local-return': 8,
    'inter-server-send': fixture.INTER_CORES, 'final-independent': FINAL_CORES,
    'joint-producer': 8 + fixture.INTER_CORES + REDUCERS, 'final-from-producer': FINAL_CORES,
    'joint-final': 8 + fixture.INTER_CORES + REDUCERS + FINAL_CORES,
    'joint-reuse-final': 8 + fixture.INTER_CORES + REDUCERS,
}


def block_count(stage, intra_cores):
    if stage == 'local-send':
        return intra_cores
    if stage.startswith('joint-'):
        return BLOCKS[stage] - 8 + intra_cores
    return BLOCKS[stage]


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


def ensure_asset(tokens, binary, revision, intra_cores, peer_slots):
    variant = '' if (intra_cores, peer_slots) == (8, 1) else f'-intra{intra_cores}-peer{peer_slots}'
    asset = ROOT / 'assets' / f't{tokens}{variant}'
    if not (asset / 'identity.json').is_file():
        assert not asset.exists(), 'incomplete_fixture_assets'
        asset.parent.mkdir(exist_ok=True)
        try:
            fixture.prepare(SOURCE, asset, WORLD, tokens, binary, disk_backed=True, revision=revision,
                            intra_cores=intra_cores, peer_slots=peer_slots)
        except Exception:
            asset.rename(asset.with_name(asset.name + f'-failed-{time.time_ns()}'))
            raise
    identity = read_json(asset / 'identity.json')
    assert identity['candidate_commit'] == revision
    assert (identity.get('intra_cores', 8), identity.get('peer_slots', 1)) == (intra_cores, peer_slots)
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
    intra_cores = asset_id.get('intra_cores', 8)
    for row in layout['ranks']:
        rank = row['rank']
        rank_name = f'rank{rank:02}'
        inputs = case / 'input_bin' / rank_name
        golden = case / 'fixture-data' / rank_name
        args = bytearray((inputs / 'kernel_args.bin').read_bytes())
        assert len(args) == 296
        struct.pack_into('<II', args, 148, 0, REDUCERS)
        struct.pack_into('<I', args, 156, 1 if action == 'local-send' else 2)
        (inputs / 'kernel_args.bin').write_bytes(args)
        chip = workload['workload_config'][f'chip{rank}']
        chip['block_dim'] = block_count(action, intra_cores)
        for word in (18, 19):
            chip['kernel_param'][f'word{word}']['value'] = struct.unpack_from('<Q', args, word * 8)[0]
        configured_root = asset_id['runtime_root']
        chip['kernel_file'] = chip['kernel_file'].replace(configured_root, str(case))
        for field in chip['kernel_param'].values():
            if 'input_bin' in field:
                field['input_bin'] = field['input_bin'].replace(configured_root, str(case))
        status = b'\xa5' * STRIPE
        if action == 'local-send':
            status = bytes(intra_cores * 4) + status[intra_cores * 4:]
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
            stages = asset_id['chunks'] * asset_id['groups']
            for reducer in range(REDUCERS):
                struct.pack_into('<I', completed, (stages * 8 + reducer) * STRIPE, stages)
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
            record.update(sst_wall_seconds=round(time.monotonic() - start, 3), returncode=proc.returncode,
                          sst_peak_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
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


CQ_PROBE_QUEUES = ((2, 0, 16), (2, 1, 36), (4, 0, 16), (4, 1, 36), (1, 2, 17), (1, 3, 37),
                   (5, 2, 17), (5, 3, 37), (8, 9, 36), (11, 8, 16), (11, 9, 36))
INTER_CQ_PROBE_QUEUES = ((0, 24, 8), (0, 24, 28), (0, 25, 8), (0, 25, 28),
                         (0, 30, 11), (0, 30, 31), (0, 31, 11), (0, 31, 31),
                         (0, 56, 8), (0, 56, 28), (0, 57, 8), (0, 57, 28),
                         (0, 62, 11), (0, 62, 31), (0, 63, 11), (0, 63, 31))


def cq_probe_queues(rank, all_local, inter):
    if inter:
        return [(peer, slot) for sender, peer, slot in INTER_CQ_PROBE_QUEUES if sender == rank]
    if not all_local:
        return [(peer, slot) for sender, peer, slot in CQ_PROBE_QUEUES if sender == rank]
    from return_case import local_slot
    begin = rank // 8 * 8
    return [(peer, local_slot(peer - begin)) for peer in range(begin, begin + 8) if peer != rank]


def bind_return_probe(case, cq_probe=False, joint_probe=False, all_local_cq=False, inter_cq=False):
    from runtime import BASES, WORLD, PHYSICAL_QPS, physical_queue_addresses
    layout = read_json(case / 'export-layout.json')
    workload = read_json(case / 'workload.yml')
    state_bytes = WORLD * PHYSICAL_QPS * 64
    for row in layout['ranks']:
        chip = workload['workload_config'][f"chip{row['rank']}"]
        if joint_probe:
            rank_input = case / 'input_bin' / f"rank{row['rank']:02d}"
            local_workspace, = struct.unpack_from('<Q', (rank_input / 'kernel_args.bin').read_bytes())
            stages = (rank_input / 'masks.bin').stat().st_size // 4
            segment = dict(addr=local_workspace + (3 * WORLD + 96) * STRIPE, size=stages * 8 * STRIPE)
            row['exports']['gather_done'] = segment
            chip['output_param']['gather_done'] = segment
        planes = [('sq_head', 1), ('sq_tail', 2)]
        if cq_probe:
            planes.append(('cq_tail', 4))
        for name, plane in planes:
            segment = dict(addr=row['sys_va_base_addr'] + BASES['queue_state'] + plane * state_bytes,
                           size=state_bytes)
            row['exports'][name] = segment
            chip['output_param'][name] = segment
        if cq_probe:
            for peer, slot in cq_probe_queues(row['rank'], all_local_cq, inter_cq):
                physical = physical_queue_addresses(row['rank'], peer, slot)
                for prefix in ('sq', 'cq'):
                    name = f'{prefix}-{peer}-{slot}'
                    segment = dict(addr=physical[prefix], size=1536)
                    row['exports'][name] = segment
                    chip['output_param'][name] = segment
    write_json(case / 'export-layout.json', layout)
    write_json(case / 'workload.yml', workload)


def observe_local_return(case, environment, ready_probe):
    from addressed_exports import reconstruct_die_output
    from return_case import local_slot
    from collections import Counter
    layout = read_json(case / 'export-layout.json')
    observations = {name: Counter() for name in ('status', 'local_ready', 'gateway_ready', 'sq_head', 'sq_tail')}
    missing = Counter()
    trace = {key: Counter() for key in ('ctor', 'stage', 'raw', 'mask', 'step')}
    decoder = Path(environment['decoder_library'])
    for row in layout['ranks']:
        rank = row['rank']
        base = row['sys_va_base_addr']
        for name, histogram in observations.items():
            segment = row['exports'][name]
            paths = [case / 'bin' / f'david_{rank}_die{die}_{name}.bin' for die in (1, 2)]
            try:
                data = reconstruct_die_output(paths, segment['addr'] - base, segment['size'], decoder)
            except (FileNotFoundError, ValueError):
                missing[name] += 1
                continue
            if name in ('sq_head', 'sq_tail'):
                offsets = [(peer * 40 + local_slot(peer % 8)) * 64
                           for peer in range(rank // 8 * 8, rank // 8 * 8 + 8) if peer != rank]
            elif name == 'status':
                offsets = [lane * 32 for lane in range(8)]
                if ready_probe:
                    for index, histogram in enumerate(trace.values(), 1):
                        histogram.update(struct.unpack_from('<I', data, offset + index * 4)[0] for offset in offsets)
            else:
                offsets = [i for i in range(0, len(data), 8)]
            size = '<q' if name.endswith('ready') else '<I'
            histogram.update(struct.unpack_from(size, data, offset)[0] for offset in offsets)
    report = dict(probe_histograms=observations, probe_missing_ranks=missing)
    if ready_probe:
        report['readiness_trace'] = trace
    return report


def observe_joint(case, environment, intra_cores, joint_probe=False, inter_probe=False):
    from addressed_exports import reconstruct_die_output
    from collections import Counter
    layout = read_json(case / 'export-layout.json')
    decoder = Path(environment['decoder_library'])
    missing = Counter()
    status = {role: Counter() for role in ('intra', 'return', 'inter', 'gateway', 'final')}
    flags = {name: Counter() for name in ('gateway_ready', 'completed', 'local_ready')}
    pending = Counter()
    samples = []
    phases = Counter()
    lanes = []
    gathered_flags = Counter()
    inter_trace = []
    for row in layout['ranks']:
        rank = row['rank']
        base = row['sys_va_base_addr']
        data = {}
        names = (*flags, 'sq_head', 'sq_tail', 'status')
        for name in (*names, 'gather_done') if joint_probe else names:
            segment = row['exports'][name]
            paths = [case / 'bin' / f'david_{rank}_die{die}_{name}.bin' for die in (1, 2)]
            try:
                data[name] = reconstruct_die_output(paths, segment['addr'] - base, segment['size'], decoder)
            except (FileNotFoundError, ValueError):
                missing[name] += 1
        if 'gather_done' in data:
            masks = (case / 'input_bin' / f'rank{rank:02d}' / 'masks.bin').read_bytes()
            for stage, (mask,) in enumerate(struct.iter_unpack('<I', masks)):
                for source in range(8):
                    if not mask & (1 << source):
                        continue
                    index = (stage * 8 + source) * STRIPE
                    value, = struct.unpack_from('<q', data['gather_done'], index)
                    gathered_flags[f'{stage}/{"present" if value == 1 else "missing"}'] += 1
        if 'status' in data:
            if inter_probe and rank == 0:
                for owner in (8, 11):
                    stamp, = struct.unpack_from('<I', data['status'], 2 * STRIPE + owner * 32 + 4)
                    inter_trace.append(dict(owner=owner, step=stamp >> 24, chunk=(stamp >> 16) & 255,
                                            peer=(stamp >> 8) & 255, qp=stamp & 255))
            for role, offset, count, stride in (
                ('intra', 0, intra_cores, 4), ('return', STRIPE, 8, 32),
                ('inter', 2 * STRIPE, 16, 32), ('gateway', (intra_cores + 16) * 4, REDUCERS, 4),
                ('final', 3 * STRIPE, FINAL_CORES, 32),
            ):
                status[role].update(struct.unpack_from('<I', data['status'], offset + lane * stride)[0]
                                    for lane in range(count))
            if joint_probe:
                for peer in range(0, 8, 8 // intra_cores):
                    trace, = struct.unpack_from('<I', data['status'], STRIPE + peer * 32 + 4)
                    phase, chunk, target = trace >> 24, (trace >> 16) & 255, (trace >> 8) & 255
                    if phase == 2 and target < 8:
                        wait, step = struct.unpack_from('<2I', data['status'], STRIPE + target * 32 + 8)
                    else:
                        wait, step = None, None
                    phases[f'{phase}/{chunk}'] += 1
                    if rank < 4:
                        lanes.append(dict(rank=rank, peer=peer, phase=phase, chunk=chunk, target=target,
                                          gateway_stage=wait, return_step=step))
        for name, histogram in flags.items():
            if name not in data:
                continue
            want = (case / 'fixture-data' / f'rank{rank:02}' / f'golden-{name}.bin').read_bytes()
            for offset, (expected,) in enumerate(struct.iter_unpack('<q', want)):
                if expected == MAGIC:
                    actual, = struct.unpack_from('<q', data[name], offset * 8)
                    histogram['matched' if actual == MAGIC else 'missing'] += 1
        if 'sq_head' not in data or 'sq_tail' not in data:
            continue
        for flat in range(len(data['sq_head']) // 64):
            head, = struct.unpack_from('<I', data['sq_head'], flat * 64)
            tail, = struct.unpack_from('<I', data['sq_tail'], flat * 64)
            if head > tail:
                peer, slot = divmod(flat, 40)
                domain = 'local' if peer // 8 == rank // 8 else 'inter'
                pending[domain] += 1
                if len(samples) < 12:
                    samples.append(dict(rank=rank, peer=peer, slot=slot, head=head, tail=tail))
    result = dict(joint_status=status, ready_markers=flags, outstanding_sq=pending,
                  outstanding_sq_examples=samples, probe_missing_ranks=missing)
    if joint_probe:
        result['joint_trace'] = dict(phases=phases, lanes=lanes, gather_done=gathered_flags)
    if inter_probe:
        result['inter_trace'] = inter_trace
    return result


def observe_cq(case, environment, all_local=False, inter=False):
    from collections import Counter
    from addressed_exports import read_die_stripe, reconstruct_die_output
    layout = read_json(case / 'export-layout.json')
    decoder = Path(environment['decoder_library'])

    def read(rank, name):
        row = layout['ranks'][rank]
        segment = row['exports'][name]
        sources = [case / 'bin' / f'david_{rank}_die{die}_{name}.bin' for die in (1, 2)]
        return reconstruct_die_output(sources, segment['addr'] - row['sys_va_base_addr'], segment['size'], decoder)

    def entry(rank, name, index):
        row = layout['ranks'][rank]
        segment = row['exports'][name]
        assert 0 <= index < segment['size'] // 64
        sources = [case / 'bin' / f'david_{rank}_die{die}_{name}.bin' for die in (1, 2)]
        address = segment['addr'] - row['sys_va_base_addr'] + index * 64
        return read_die_stripe(sources, address, 64, decoder)

    samples = []
    counts = Counter()
    for rank in range(len(layout['ranks'])):
        selected = cq_probe_queues(rank, all_local, inter)
        if not selected:
            continue
        names = ('sq_head', 'sq_tail', 'cq_tail')
        state = [read(rank, name) for name in names]
        for peer, slot in selected:
            offset = (peer * 40 + slot) * 64
            head, tail, completion = [struct.unpack_from('<I', data, offset)[0] for data in state]
            if (all_local or inter) and head == tail:
                counts['drained'] += 1
                continue
            sample = dict(rank=rank, peer=peer, slot=slot, sq_head=head, sq_tail=tail, cq_tail=completion)
            if completion >= 1536 // 64:
                counts['next_entry_outside_export'] += 1
            else:
                try:
                    words = struct.unpack_from('<2I', entry(rank, f'cq-{peer}-{slot}', completion))
                    owner, error, entry_idx = (words[0] >> 2) & 1, words[0] >> 16, words[1] & 0xFFFF
                    sample.update(cq_next_owner=owner, cq_next_status=error, cq_next_sq_index=entry_idx)
                    counts['cq_present' if owner else 'cq_old_owner'] += 1
                except (FileNotFoundError, ValueError):
                    counts['cq_dump_missing'] += 1
                    sample['cq_dump_missing'] = True
            if 0 < head <= 1536 // 64:
                try:
                    payload = entry(rank, f'sq-{peer}-{slot}', head - 1)
                    length, = struct.unpack_from('<I', payload, 48)
                    sample.update(sq_last_nonzero=any(payload), sq_last_length=length)
                    if inter or all_local:
                        address, = struct.unpack_from('<Q', payload, 40)
                        plans = layout['ranks'][peer]
                        target_name = 'completed' if inter else 'local_ready'
                        target = plans['exports'][target_name]
                        if length == 8 and 0 <= address - target['addr'] <= target['size'] - length:
                            offset = address - target['addr']
                            sample['notification_index'] = offset // STRIPE if inter else offset % STRIPE // 8
                            if all_local:
                                sample['notification_sender'] = offset // STRIPE
                            paths = [case / 'bin' / f'david_{peer}_die{d}_{target_name}.bin' for d in (1, 2)]
                            flag = read_die_stripe(paths, address - plans['sys_va_base_addr'], 8, decoder)
                            sample['notification_visible'] = struct.unpack('<q', flag)[0] == MAGIC
                except (FileNotFoundError, ValueError):
                    sample['sq_dump_missing'] = True
            if len(samples) < 16:
                samples.append(sample)
    return dict(cq_samples=samples, cq_frontiers=counts)


def main(config):
    action, tokens, attempt, timeout = (config[key] for key in ('action', 'tokens', 'attempt', 'timeout'))
    revision = config['revision']
    assert read_json(ROOT / 'identity.json')['revision'] == revision
    use_profile = config.get('profiling', False)
    if action == 'joint-reuse-final':
        assert use_profile, 'producer reuse is bound by the profiling wrapper'
    stop_at_us = config.get('diagnostic_stop_us')
    ready_probe = config.get('ready_probe', False)
    joint_probe = config.get('joint_probe', False)
    cq_probe = config.get('cq_probe', False)
    all_local_cq = config.get('all_local_cq', False)
    inter_cq = config.get('inter_cq', False)
    inter_probe = config.get('inter_probe', False)
    intra_cores = config.get('intra_cores', 8)
    peer_slots = config.get('peer_slots', 1)
    return_qp_base = config.get('return_qp_base', 0)
    blocks = block_count(action, intra_cores)
    build_file = 'profile-build/build-summary.json' if use_profile else 'build-summary.json'
    build = read_json(ROOT / build_file)
    assert build['all_compiled'] and build['sim_send'] and build['profiling'] == use_profile
    kernel_build = read_json(ROOT / 'ready-probe-build/build-summary.json') if ready_probe else build
    if joint_probe:
        kernel_build = read_json(ROOT / 'joint-probe-build/build-summary.json')
    if inter_probe:
        kernel_build = read_json(ROOT / 'inter-probe-build/build-summary.json')
    case = ROOT / 'cases' / f't{tokens}-{action}-a{attempt}'
    assert not case.exists(), 'case_already_exists'
    record = {'candidate_commit': revision, 'stage': action, 'tokens': tokens, 'attempt': attempt,
              'world': WORLD, 'hidden': HIDDEN, 'topk': TOPK, 'dtype': 'fp32', 'sim_send': True,
              'profiling_enabled': use_profile, 'blocks': blocks, 'intra_cores': intra_cores, 'peer_slots': peer_slots,
              'gateway_reducers': REDUCERS,
              'final_cores': blocks if action == 'joint-reuse-final' else FINAL_CORES,
              'inter_cores': fixture.INTER_CORES, 'port_jetties': fixture.jetty_counts(peer_slots),
              'port0_die_mode': fixture.DIE_MODE}
    if return_qp_base:
        assert action == 'joint-final' and return_qp_base == 8
        record.update(local_return_qp_base=return_qp_base)
    started = time.monotonic()
    write_json(ROOT / 'progress.json', {'stage': 'prepare_case', 'case': case.name, 'updated_unix': time.time()})
    try:
        asset = ensure_asset(tokens, object_for(LOCAL_ENTRY, build), revision, intra_cores, peer_slots)
        record['routes_sha256'] = digest(asset / 'routes.json')
        if action in ('local-send', 'gateway-reduce'):
            local_phase(case, asset, action)
        else:
            from return_case import prepare_return_case
            record.update(prepare_return_case(case, asset, action, object_for(ENTRIES[action], kernel_build),
                                              config.get('source_case'), revision, return_qp_base))
        if use_profile:
            from profile_case import bind
            if action in ('local-send', 'gateway-reduce'):
                linked = object_for(ENTRIES[action], build).read_bytes()
                (case / 'kernel/inter_server_send.o').write_bytes(linked + bytes(10485760 - len(linked)))
            bind(case, blocks)
        record['kernel_sha256'] = digest(case / 'kernel/inter_server_send.o')
        record['source_object_sha256'] = digest(object_for(ENTRIES[action], kernel_build))
        if stop_at_us is not None:
            assert action in ('local-return', 'joint-final')
            origin = f't4-{action}-a1'
            original = read_json(case.parent / origin / 'phase-result.json')
            assert original['status'] in ('timed_out', 'interrupted') or (
                original['status'] == 'failed' and original.get('returncode') == -9)
            keys = ('candidate_commit', 'tokens', 'stage', 'routes_sha256')
            if not ready_probe and not joint_probe and not inter_probe:
                keys += ('kernel_sha256', 'source_object_sha256')
            for key in keys:
                assert original[key] == record[key], key
            record.update(diagnostic_only=True, stop_at_us=stop_at_us, diagnostic_source=origin)
            bind_return_probe(case, cq_probe, joint_probe, all_local_cq, inter_cq)
        env = stage_platform(case, config['platform_sha256'])
        import yaml
        model_config = case / 'v310_config/davidV100_simulator_config.yml'
        scalar = yaml.safe_load(model_config.read_text())['simulator_config']['su']
        component, level = scalar['component'], scalar['params']['lsu_level']
        assert component == 'V310.ScalarUnitL1' and int(level) == 1, 'SST perf model required'
        record['model_config'] = dict(component=component, lsu_level=int(level), sha256=digest(model_config))
        record['environment_sha256'] = digest(case / 'sst-environment.json')
        write_json(ROOT / 'progress.json', {'stage': 'sst', 'case': case.name, 'updated_unix': time.time()})
        threads = config.get('sst_threads', 8)
        record['sst_threads'] = threads
        run_sst(case, env, timeout, record, stop_at_us, threads)
        if stop_at_us is None:
            check_outputs(case, env, record)
            if use_profile:
                from profile_case import summarize
                record.update(summarize(case, WORLD, blocks))
            record['status'] = 'sst_and_checker_passed'
        else:
            if action == 'joint-final':
                record.update(observe_joint(case, env, intra_cores, joint_probe, inter_probe))
                if cq_probe:
                    record.update(observe_cq(case, env, all_local_cq, inter_cq))
            else:
                record.update(observe_local_return(case, env, ready_probe))
            record['status'] = 'diagnostic_snapshot'
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
    return 0 if record['status'] in ('sst_and_checker_passed', 'diagnostic_snapshot') else 1


if __name__ == '__main__':
    raise SystemExit(main(json.loads(sys.argv[1])))
