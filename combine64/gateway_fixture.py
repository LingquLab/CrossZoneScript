"""Preload post-dispatch FP32 contributions and checked standalone GatewayReduce fixtures."""
from array import array
import hashlib
import json
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys

from gateway_profile import profile_image

WORLD = 64
HIDDEN = 128
EXPERTS = 256
TOPK = 6
STRIPE = 512
SEED = 20261124
MICRO_CHUNKS = 4
ENTRY = 'tilexr_moonep_combine_ht_kernel'
GUARD = struct.pack('<f', -17.)
ADDRESSES = dict(input=0x404000000, masks=0x406000000, profile=0x430000000)


class Spool:
    def __init__(self, root, images):
        root.mkdir()
        self.images = []
        for name, address, data in images:
            path = root / name
            path.write_bytes(data)
            self.images.append((name, address, path))

    def __iter__(self):
        for name, address, path in self.images:
            yield dict(name=name, addr=address, data=path.read_bytes())


def random_routes(tokens):
    rng = random.Random(SEED)
    gateways = [[] for _ in range(WORLD)]
    route_bytes = bytearray()
    for source in range(WORLD):
        for token in range(tokens):
            selected = rng.sample(range(EXPERTS), TOPK)
            route_bytes.extend(selected)
            groups = {}
            for slot, expert in enumerate(selected):
                owner = expert // (EXPERTS // WORLD)
                groups.setdefault(owner // 8, []).append((owner, expert, slot))
            for contributions in groups.values():
                gateways[contributions[0][0]].append((source, token, contributions))
    return gateways, hashlib.sha256(route_bytes).hexdigest()


def micro_routes(tokens):
    # Two expert outputs are local to the gateway; four use preloaded gather rows.
    contributions = [(0, 0, 0), (0, 1, 1), (1, 4, 2), (2, 8, 3), (3, 12, 4), (4, 16, 5)]
    return [[(0, token, contributions) for token in range(tokens)], *([] for _ in range(WORLD - 1))]


def build_host_tools(source, support, metadata):
    helper, probe = metadata / 'gateway-args', metadata / 'abi-probe'
    common = ['c++', '-std=c++17', '-O2', '-DTILEXR_COMBINE_HT_SIM_SEND=1',
              '-DTILEXR_COMBINE_HT_SST_FP32=1', '-I' + str(source / 'include')]
    subprocess.run([*common, '-I' + str(source / 'src/moonep/combine_ht/common'),
                    str(Path(__file__).with_name('gateway_args.cpp')),
                    str(source / 'src/moonep/combine_ht/host/combine_ht_prefill.cpp'),
                    '-o', str(helper)], check=True)
    subprocess.run([*common, str(support / 'abi_probe.cpp'), '-o', str(probe)], check=True)
    return helper, probe


def make_rank(rank, tokens, chunk, cores, items, micro, metadata, helper, probe, golden):
    import runtime
    active = not micro or rank == 0
    if not active:
        tokens = chunk
    workload_world = 8 if micro and active else WORLD
    chunks = tokens // chunk
    base = rank * runtime.GM_BYTES
    addresses = {**runtime.BASES, **ADDRESSES}
    name = f'rank{rank:02}'
    prefix = metadata / name
    out = golden / name
    out.mkdir()
    # Rows are dense within the gateway's own input and gather regions.
    input_count = 0
    gather_count = 0
    fp32_input = bytearray()
    gathered = bytearray()
    expected_rows = {}
    forward_capacity = tokens * TOPK
    forward = array('i', [0]) * (workload_world * forward_capacity * 6)
    records = {}
    reduce_tokens = 0
    for source, token, contributions in items:
        expected = [0.0] * HIDDEN
        primary = input_count
        entries = []
        for index, (owner, expert, slot) in enumerate(contributions):
            values = [1 + (source * 17 + token * 3 + expert * 5 + slot * 11 + h % 13) / 256
                      for h in range(HIDDEN)]
            payload = struct.pack('<128f', *values)
            expected = [lhs + rhs for lhs, rhs in zip(expected, values)]
            if owner == rank:
                row = input_count
                input_count += 1
                fp32_input.extend(payload)
                gather = -1
            else:
                row = -1
                gather = gather_count
                gather_count += 1
                gathered.extend(payload)
            if index:
                entries.append((owner, row, gather))
        if entries:
            reduce_tokens += 1
            expected_rows[primary] = struct.pack('<128f', *expected)
        else:
            entries.append((-1, 0, -1))
        records.setdefault((source, token // chunk), []).append((token, primary, entries))
    if not fp32_input:
        fp32_input.extend(GUARD * HIDDEN)
    if not gathered:
        gathered.extend(GUARD * HIDDEN)
    config = (rank, workload_world, tokens, HIDDEN, TOPK, len(fp32_input) // STRIPE,
              len(gathered) // STRIPE, chunk, 1, int(not active), cores, 2, 0, 8, 8, 1, 1, 2, 1)
    prefix.with_suffix('.config').write_bytes(struct.pack('<19I', *config))
    subprocess.run([str(helper), str(prefix)], check=True)
    raw = prefix.with_suffix('.args').read_bytes()
    layout = struct.unpack('<6I6Q', prefix.with_suffix('.layout').read_bytes())
    schedule = prefix.with_suffix('.schedule').read_bytes()
    assert len(raw) == 296 and layout[0] == chunks and layout[3] == forward_capacity
    groups = layout[1]
    gather_offset, partial_offset, workspace_bytes = layout[6:9]
    assert partial_offset - gather_offset == len(gathered)
    assert workspace_bytes - partial_offset == len(fp32_input)
    schedule_words = len(schedule) // 4 // workload_world
    peer_groups = struct.unpack_from(f'<{workload_world}I', schedule, 4 * (rank * schedule_words + workload_world))
    ranges = array('i')
    masks = [0] * (groups * chunks)
    for source in range(workload_world):
        count = 0
        for chunk_index in range(chunks):
            start = count
            for token, primary, entries in records.get((source, chunk_index), ()):
                end = count + len(entries)
                assert end <= forward_capacity
                for owner, row, gather in entries:
                    offset = 6 * (source * forward_capacity + count)
                    forward[offset:offset + 6] = array('i', (end, token, primary, owner, row, gather))
                    count += 1
                    if gather >= 0:
                        masks[peer_groups[source] * chunks + chunk_index] |= 1 << (owner % 8)
            ranges.extend((start, count))
    workspace = bytearray(workspace_bytes)
    completed_offset = (3 * workload_world + 96) * STRIPE
    stages = chunks * groups
    for stage, mask in enumerate(masks):
        for sender in range(8):
            if mask & (1 << sender):
                struct.pack_into('<Q', workspace, completed_offset + (stage * 8 + sender) * STRIPE, 1)
    workspace[layout[10]:gather_offset] = schedule
    workspace[gather_offset:partial_offset] = gathered
    workspace[partial_offset:] = GUARD * (len(fp32_input) // 4)
    completed_size = (stages * 8 + cores) * STRIPE
    completed = bytearray(workspace[completed_offset:completed_offset + completed_size])
    if active:
        for lane in range(cores):
            struct.pack_into('<I', completed, (stages * 8 + lane) * STRIPE, stages)
    output = bytearray(workspace[partial_offset:])
    for row, value in expected_rows.items():
        output[row * STRIPE:(row + 1) * STRIPE] = value
    for key, data in [('output', output), ('returned', gathered), ('completed', completed)]:
        (out / f'golden-{key}.bin').write_bytes(data)
    profile = profile_image(base + addresses['profile'], cores, 32768 if micro and active else 4096)
    runtime_args = [str(probe), str(rank), '128', '128', '6', '49152', '49152',
                    '2026', '-', '64', '8', '128', '1', '16', '1', '1']
    abi = json.loads(subprocess.check_output(runtime_args))
    images = runtime.runtime_images(abi, rank, workspace_bytes, world=WORLD, port0_die_mode='chip_topo')
    images.update(workspace=workspace, input=fp32_input, forward=forward.tobytes(), ranges=ranges.tobytes(),
                  masks=struct.pack(f'<{len(masks)}i', *masks), profile=profile)
    guards = []
    for key, address in [('guard_before', addresses['workspace'] - STRIPE),
                         ('guard_after', addresses['workspace'] + workspace_bytes)]:
        images[key] = b'\xde' * STRIPE
        addresses[key] = address
        guards.append(dict(name=key, addr=base + address, size=STRIPE, expected_byte=222))
    ws = base + addresses['workspace']
    exports = dict(output=dict(addr=ws + partial_offset, size=len(output)),
                   returned=dict(addr=ws + gather_offset, size=len(gathered)),
                   completed=dict(addr=ws + completed_offset, size=completed_size),
                   profile=dict(addr=base + addresses['profile'], size=len(profile)))
    fields = [dict(name=f'word{i}', offset=i * 8, type='uint64_t', value=word)
              for i, (word,) in enumerate(struct.iter_unpack('<Q', raw))]
    spool = Spool(metadata / f'{name}-images', ((key, base + addresses[key], data) for key, data in images.items()))
    spec = dict(rank=rank, abi_size=len(raw), fields=fields, block_dim=cores,
                gm_size=runtime.GM_BYTES, sys_va_base_addr=base, ub_base_addr=0x80000, stack_base_addr=0x100000,
                param_base_addr=base + 0x80000000, binary_addr=base + 0x40000000,
                images=spool, exports=exports, guards=guards)
    port0_die = runtime.resolve_port0_die(rank, 'chip_topo')
    assert all(runtime.physical_qp(qp, rank=rank, port0_die_mode='chip_topo') // runtime.SLOTS_PER_DIE == port0_die
               for qp in range(16))
    return spec, dict(rank=rank, active=active, groups=groups, chunks=chunks,
                      port0_die=port0_die, reduced_tokens=reduce_tokens, tokens=len(items),
                      gathered_rows=gather_count, contributions=sum(len(parts) for _, _, parts in items))


def prepare(source, case, binary, cores, chunk, micro, runtime_root=None):
    support = source / 'tools/unitest/simulator/experiments/inter_server_send/sst'
    sys.path.insert(0, str(support))
    import loader
    assert (micro and cores == 1 and chunk in (16, 32, 64, 128, 512)) or (
        not micro and cores in (8, 16, 24, 32) and chunk in (16, 32, 64, 128))
    tokens = chunk * MICRO_CHUNKS if micro else 128
    routes, routes_hash = (micro_routes(tokens), None) if micro else random_routes(tokens)
    metadata, golden = case / '.fixture', case / 'fixture-data'
    metadata.mkdir(parents=True)
    golden.mkdir()
    helper, probe = build_host_tools(source, support, metadata)
    padded = metadata / 'kernel-padded.o'
    padded.write_bytes(binary.read_bytes().ljust(10485760, b'\0'))
    specs, rank_stats = zip(*(make_rank(rank, tokens, chunk, cores, routes[rank], micro,
                                      metadata, helper, probe, golden) for rank in range(WORLD)))
    stage = case / '.loader'
    manifest = loader.prepare_loader(stage, str(runtime_root or case), padded, loader.sha256(padded), ENTRY, specs)
    for path in stage.iterdir():
        path.rename(case / path.name)
    stage.rmdir()
    shutil.rmtree(metadata)
    stats = dict(world=WORLD, tokens=tokens, chunk_tokens=chunk, cores=cores, hidden=HIDDEN, topk=TOPK,
                 experts=EXPERTS, dtype='fp32', seed=None if micro else SEED, routes_sha256=routes_hash,
                 chunks=tokens // chunk, port0_die_mode='chip_topo',
                 port0_die_histogram={str(die): sum(row['port0_die'] == die for row in rank_stats) for die in (0, 1)},
                 rank_stats=rank_stats, kernel_sha256=manifest['kernel_sha256'])
    assert stats['port0_die_histogram'] == {'0': 32, '1': 32}
    assert sum(row['contributions'] for row in rank_stats) == (tokens if micro else WORLD * tokens) * TOPK
    (case / 'fixture.json').write_text(json.dumps(stats, indent=2))
    return stats
