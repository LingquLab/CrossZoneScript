# Copyright (c) 2026 TileXR Project.
"""Prepare registered buffers for independent and joint HT return cases."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

from fixture import DIE_MODE, INTER_CORES, DiskImages, GUARD, jetty_counts
from sim_case import ENTRIES, FINAL_CORES, MAGIC, REDUCERS, STRIPE, SUPPORT, WORLD, block_count

WORKSPACE = 0x200000000
LOCAL_RESOURCES = {
    'localComm': 0x2FF100000, 'localInfo': 0x300080000, 'localRegistry': 0x3000A0000,
    'localSq': 0x317000000, 'localCq': 0x318000000, 'localMem': 0x319000000,
}
EXTRA = {
    'serverMask': 0x40C000000,
    'returnForward': 0x40D000000, 'output': 0x40E000000, 'prefill': 0x405000000,
    'masks': 0x406000000, 'backward': 0x407000000, 'backward_counts': 0x408000000,
    'backward_ranges': 0x409000000,
}


def local_slot(qp, port0_die=0):
    return (port0_die if qp % 2 == 0 else 1 - port0_die) * 20 + 16 + qp // 2


def read_json(path):
    return json.loads(path.read_text())


def send_config(abi):
    fields = [dict(field, name=field['name'].split('.', 1)[1], offset=field['offset'] - 48)
              for field in abi['fields'][6:]]
    from loader import pack_arguments
    return {field['name']: field['value'] for field in fields}, pack_arguments(fields, abi['config_size'])[0]


def station_gold(routes, rank, tokens, local_only):
    expected = bytearray(GUARD * (tokens * 8 * 128))
    masks, output = [], bytearray()
    for token in range(tokens):
        route = routes[rank * tokens + token]
        assert (route['source'], route['token']) == (rank, token)
        values_by_server = {}
        rows = []
        for slot, expert in enumerate(route['experts']):
            server = expert // 32
            row = [1 + (rank * 17 + token * 3 + expert * 5 + slot * 11 + h % 13) / 256 for h in range(128)]
            values_by_server.setdefault(server, []).append(row)
            rows.append(row)
        masks.append(sum(1 << server for server in values_by_server))
        output.extend(struct.pack('<128f', *map(sum, zip(*rows))))
        for server, rows in values_by_server.items():
            if not local_only or rank // 8 == server:
                offset = (token * 8 + server) * STRIPE
                struct.pack_into('<128f', expected, offset, *map(sum, zip(*rows)))
    return expected, struct.pack(f'<{tokens}Q', *masks), output


def success_status(size, count, stride=32, start=0):
    status = bytearray(b'\xa5' * size)
    for index in range(count):
        struct.pack_into('<I', status, start + index * stride, 0)
    return status


def local_communication(runtime, images, abi, local_bytes, base, return_qp_base=0):
    control = abi['runtime_abi']
    sq, cq, memory = bytearray(), bytearray(), bytearray()
    helper_mem = bytearray(WORLD * 32 * control['mem_bytes'])
    model_memory = bytearray(images['mem_info'])
    qp_count = 8 + return_qp_base
    rank = base // runtime.GM_BYTES
    for peer in range(WORLD):
        for qp in range(qp_count):
            # The inter-server worker never sends to ranks on its own server.
            # Those physical slots can hold the second local queue bank.
            physical = local_slot(qp, runtime.resolve_port0_die(rank, DIE_MODE)) if qp < 8 else (
                runtime.physical_qp(qp - 8, rank=rank, port0_die_mode=DIE_MODE))
            slot = peer * runtime.PHYSICAL_QPS + physical
            width = control['mem_bytes']
            at = slot * width
            entry = model_memory[at:at + width]
            if qp < 8 or rank // 8 == peer // 8:
                struct.pack_into('<I', entry, 16, (local_bytes + 2097151) // 2097152 * 2097152)
                struct.pack_into('<Q', entry, 24, peer * runtime.GM_BYTES + WORKSPACE)
                model_memory[at:at + width] = entry
            for output, name, size in ((sq, 'sq_ctx', control['sq_bytes']), (cq, 'cq_ctx', control['cq_bytes'])):
                output.extend(images[name][slot * size:(slot + 1) * size])
            memory.extend(entry)
            offset = (peer * 32 + qp) * width
            helper_mem[offset:offset + width] = entry
    comm = bytearray(images['commArgs'])
    struct.pack_into('<I', comm, 16, 0x400 | 0x1000)
    registry = bytearray(images['registry'])
    for peer in range(WORLD):
        address = peer * runtime.GM_BYTES + WORKSPACE
        struct.pack_into('<Q', comm, control['comm_peer_mems'] + peer * 8, address)
        struct.pack_into('<QQ', registry, control['registry_regions'] + peer * 16, address, local_bytes)
    struct.pack_into('<Q', comm, control['comm_peer_bytes'], local_bytes)
    struct.pack_into('<Q', comm, control['comm_udma_info'], base + LOCAL_RESOURCES['localInfo'])
    struct.pack_into('<Q', comm, control['comm_udma_registry'], base + LOCAL_RESOURCES['localRegistry'])
    info = bytearray(images['udma_info'])
    struct.pack_into('<I', info, 0, qp_count)
    for name, key in (('info_sq', 'localSq'), ('info_cq', 'localCq'), ('info_mem', 'localMem')):
        struct.pack_into('<Q', info, control[name], base + LOCAL_RESOURCES[key])
    images.update(mem_info=model_memory, localComm=comm, localInfo=info,
                  localRegistry=registry, localSq=sq, localCq=cq, localMem=memory)
    return helper_mem


def position_guards(images, addresses, regions, base):
    guards = []
    for region, size in regions.items():
        address = addresses[region]
        for side, at in (('before', address - STRIPE), ('after', address + size)):
            name = f'{region}_{side}'
            images[name] = b'\xde' * STRIPE
            addresses[name] = at
            guards.append({'name': name, 'addr': base + at, 'size': STRIPE, 'expected_byte': 222})
    return guards


def prepare_return_case(case, asset, action, binary, source_case, revision, return_qp_base=0):
    sys.path.insert(0, str(SUPPORT))
    import loader
    import runtime

    joint = action.startswith('joint-')
    producer = action == 'joint-producer'
    final = action.startswith('final-')
    local = action == 'local-return'
    remote = action == 'inter-server-send'
    from_producer = action == 'final-from-producer'
    identity = read_json(asset / 'identity.json')
    tokens, chunks, groups = (identity[key] for key in ('tokens', 'chunks', 'groups'))
    intra_cores = identity.get('intra_cores', 8)
    peer_slots = identity.get('peer_slots', 1)
    port0, port1 = jetty_counts(peer_slots)
    blocks = block_count(action, intra_cores)
    own_reduce = action == 'joint-reuse-final'
    final_blocks = INTER_CORES if own_reduce else FINAL_CORES
    routes = read_json(asset / 'routes.json')
    assert len(routes) == WORLD * tokens
    if from_producer:
        assert source_case and Path(source_case).name == source_case
        predecessor = case.parent / source_case
        previous = read_json(predecessor / 'phase-result.json')
        assert previous['status'] == 'sst_and_checker_passed'
        assert (previous['candidate_commit'], previous['stage'], previous['tokens']) == (revision, 'joint-producer', tokens)
        assert previous['routes_sha256'] == loader.sha256(asset / 'routes.json')
    else:
        assert source_case is None

    import time
    temporary = case.with_name(case.name + f'-prepare-{time.time_ns()}')
    temporary.mkdir(parents=True)
    golden = temporary / 'fixture-data'
    golden.mkdir()
    padded = temporary / 'kernel.o'
    raw = binary.read_bytes()
    padded.write_bytes(raw + bytes(10485760 - len(raw)))
    specs = []
    predecessor_digest = hashlib.sha256()
    for rank in range(WORLD):
        rank_name = f'rank{rank:02}'
        local_input = asset / 'loader/input_bin' / rank_name
        local_golden = asset / 'fixture-data' / rank_name
        rows = WORLD * tokens * 6
        abi = json.loads(subprocess.check_output([
            str(asset / 'abi-probe'), str(rank), str(tokens), '128', '6', str(rows), str(rows),
            str(MAGIC), '-', '64', str(REDUCERS), str(tokens // chunks), '1', str(INTER_CORES),
            str(peer_slots), '1', str(port0), str(port1), str(intra_cores),
        ]))
        cfg, config_bytes = send_config(abi)
        assert (cfg['rank'], cfg['chunks'], cfg['groups'], cfg['reducerCount'], cfg['peerSlots'],
                abi['forward_record_words']) == (rank, chunks, groups, REDUCERS, peer_slots, 8)
        base = rank * runtime.GM_BYTES
        addresses = dict(runtime.BASES, **EXTRA)
        local_ready, local_signal, _, local_offset, joint_bytes = struct.unpack_from(
            '<5Q', (asset / 'metadata' / f'{rank_name}.pipeline_layout').read_bytes(), 72 + len(config_bytes))
        assert local_ready == cfg['workspaceBytes']
        ready_stride = (groups * chunks * 8 + STRIPE - 1) // STRIPE * STRIPE
        local_stride = (chunks * 8 + STRIPE - 1) // STRIPE * STRIPE
        assert local_signal == local_ready + 8 * local_stride
        return_bytes = joint_bytes if joint else local_signal + 8 * STRIPE
        ret_address = addresses['workspace']
        images = runtime.runtime_images(abi, rank, return_bytes, world=WORLD, port0_die_mode=DIE_MODE)
        if joint or local:
            addresses.update(LOCAL_RESOURCES)
            helper_memory = local_communication(runtime, images, abi, return_bytes, base, return_qp_base)
        initial = bytearray(return_bytes)
        inp = (local_input / 'input.bin').read_bytes()
        partial = (local_golden / 'golden-output.bin').read_bytes()
        assert len(inp) == len(partial) == rows * STRIPE
        initial[:len(inp)] = inp
        initial[cfg['partialOffset']:cfg['readyOffset']] = GUARD * (len(partial) // 4) if joint else partial
        returned, mask, final_output = station_gold(routes, rank, tokens, local)
        initial[cfg['returnOffset']:cfg['signalOffset']] = GUARD * (len(returned) // 4)
        ready = bytearray(REDUCERS * ready_stride)
        for reducer in range(REDUCERS):
            for stage in range(chunks * groups):
                struct.pack_into('<q', ready, reducer * ready_stride + stage * 8, MAGIC)
        assert len(ready) == cfg['completionOffset'] - cfg['readyOffset']
        completion = bytearray(cfg['returnOffset'] - cfg['completionOffset'])
        if not local:
            for gateway in range(WORLD):
                if gateway // 8 != rank // 8:
                    for chunk in range(chunks):
                        for lane in range(port0 + port1):
                            slot = (gateway * chunks + chunk) * (port0 + port1) + lane
                            struct.pack_into('<q', completion, slot * STRIPE, MAGIC)
        flags = bytearray(8 * local_stride)
        for gateway in range(8):
            for chunk in range(chunks):
                struct.pack_into('<q', flags, gateway * local_stride + chunk * 8, MAGIC)
        schedule = (asset / 'metadata' / f'{rank_name}.schedule').read_bytes()
        assert len(schedule) == cfg['workspaceBytes'] - cfg['scheduleOffset']
        initial[cfg['scheduleOffset']:cfg['workspaceBytes']] = schedule
        if local or remote or final:
            initial[cfg['readyOffset']:cfg['completionOffset']] = ready
        if remote:
            for token in range(tokens):
                start = (token * 8 + rank // 8) * STRIPE
                initial[cfg['returnOffset'] + start:cfg['returnOffset'] + start + STRIPE] = returned[start:start + STRIPE]
            initial[local_ready:local_ready + len(flags)] = flags
        if final:
            for field, offset, reference in (('returned', cfg['returnOffset'], returned),
                                              ('completed', cfg['completionOffset'], completion),
                                              ('local_ready', local_ready, flags)):
                payload = (predecessor / 'actual' / rank_name / f'{field}.bin').read_bytes() if from_producer else reference
                assert payload == reference, (rank, field, 'predecessor_mismatch')
                predecessor_digest.update(payload)
                initial[offset:offset + len(payload)] = payload

        status_addr = addresses['status']
        gold = {}
        exports = {}
        def export(name, address, expected):
            gold[name] = expected
            exports[name] = {'addr': base + address, 'size': len(expected)}

        images.update(serverMask=mask, output=b'\xa5' * len(final_output))
        images['workspace'] = initial
        for name in ('counts', 'ranges'):
            images[name] = (local_input / (name + '.bin')).read_bytes()
        forward = (local_input / 'forward.bin').read_bytes()
        return_forward = b''.join(struct.pack('<8i', *row, 0, 0) for row in struct.iter_unpack('<6i', forward))
        images['returnForward' if joint else 'forward'] = return_forward
        pointer = lambda name: base + addresses[name]
        export('returned', ret_address + cfg['returnOffset'], returned)
        export('gateway_ready', ret_address + cfg['readyOffset'], ready)
        export('completed', ret_address + cfg['completionOffset'], completion)
        export('local_ready', ret_address + local_ready, flags)
        regions = {'workspace': return_bytes}
        out_status = None if joint else success_status(512, blocks)
        args = None
        if joint:
            local_ws = (local_input / 'workspace.bin').read_bytes()
            raw_local = bytearray((local_input / 'kernel_args.bin').read_bytes())
            gather_offset, partial_offset, _, _, schedule_offset = struct.unpack_from('<5Q', raw_local, 216)
            assert return_bytes == local_offset + partial_offset
            initial[local_offset:] = local_ws[:partial_offset]
            helper_file = temporary / rank_name
            Path(str(helper_file) + '.config').write_bytes(raw_local[112:188])
            Path(str(helper_file) + '.native_mem_info').write_bytes(helper_memory)
            for name in ('masks', 'backward', 'backward_counts', 'backward_ranges'):
                images[name] = (local_input / (name + '.bin')).read_bytes()
                if name in ('backward', 'backward_counts'):
                    Path(str(helper_file) + '.' + name).write_bytes(images[name])
            subprocess.run([str(asset / 'prefill-tool'), str(helper_file)], check=True)
            images.update(forward=forward, prefill=Path(str(helper_file) + '.joint_prefill').read_bytes())
            for offset, value in ((0, pointer('workspace') + local_offset),
                                  (8, pointer('workspace') + cfg['inputOffset']),
                                  (88, pointer('localComm')), (96, pointer('localInfo')),
                                  (104, pointer('localRegistry')), (288, local_offset)):
                struct.pack_into('<Q', raw_local, offset, value)
            bound_config = bytearray(config_bytes)
            schedule_field = next(field for field in abi['fields'] if field['name'] == 'config.scheduleOffset')
            struct.pack_into('<Q', bound_config, schedule_field['offset'] - 48, local_offset + schedule_offset)
            send_args = struct.pack('<6Q', pointer('commArgs'), pointer('workspace'),
                                    pointer('returnForward'), pointer('counts'), pointer('ranges'),
                                    base + status_addr + 2 * STRIPE) + bound_config
            args = raw_local + send_args + struct.pack('<6Q4I', base + status_addr + STRIPE, pointer('serverMask'),
                pointer('output'), base + status_addr + 3 * STRIPE, local_ready, local_signal,
                return_qp_base, 1, final_blocks, 2 if own_reduce else int(not producer))
            assert len(args) == 568
            local_status = success_status(512, intra_cores, 4)
            reduce_start = intra_cores + cfg['ownerCount']
            local_status[reduce_start * 4:(reduce_start + REDUCERS) * 4] = bytes(REDUCERS * 4)
            final_status_bytes = (final_blocks * 32 + STRIPE - 1) // STRIPE * STRIPE
            out_status = (local_status + success_status(STRIPE, 8) + success_status(STRIPE, INTER_CORES) +
                          success_status(final_status_bytes, 0 if producer else final_blocks))
            export('gather', WORKSPACE + local_offset + gather_offset, (local_golden / 'golden-returned.bin').read_bytes())
            export('partial', WORKSPACE + cfg['partialOffset'], partial)
            export('output', addresses['output'], images['output'] if producer else final_output)
            regions['output'] = len(final_output)
        elif local:
            args = struct.pack('<7Q', *(pointer(name) for name in ('localInfo', 'localRegistry', 'workspace',
                'forward', 'counts', 'ranges', 'status'))) + config_bytes
            args += struct.pack('<2Q4I', local_ready, local_signal, rank // 8 * 8, 0, 1, 0)
            assert len(args) == 248
        elif remote:
            args = struct.pack('<6Q', *(pointer(name) for name in ('commArgs', 'workspace', 'forward',
                'counts', 'ranges', 'status'))) + config_bytes
            assert len(args) == 208
        else:
            args = struct.pack('<5Q', pointer('workspace'), pointer('serverMask'), pointer('workspace') + local_ready,
                               pointer('output'), pointer('status')) + config_bytes
            assert len(args) == 200
            export('output', addresses['output'], final_output)
            regions['output'] = len(final_output)
        images['status'] = b'\xa5' * len(out_status)
        export('status', status_addr, out_status)
        regions['status'] = len(out_status)
        guards = position_guards(images, addresses, regions, base)
        dest = golden / rank_name
        dest.mkdir()
        for name, payload in gold.items():
            (dest / f'golden-{name}.bin').write_bytes(payload)
        images_on_disk = DiskImages(temporary / 'spool' / rank_name,
                                    (dict(name=name, addr=pointer(name), data=payload) for name, payload in images.items()))
        specs.append(dict(rank=rank, block_dim=blocks, abi_size=len(args),
            fields=[dict(name=f'word{i}', offset=i * 8, type='uint64_t', value=value)
                    for i, (value,) in enumerate(struct.iter_unpack('<Q', args))],
            gm_size=runtime.GM_BYTES, sys_va_base_addr=base, ub_base_addr=0x80000, stack_base_addr=0x100000,
            param_base_addr=base + 0x80000000, binary_addr=base + 0x40000000,
            images=images_on_disk, exports=exports, guards=guards))
    loader.prepare_loader(case, str(case), padded, loader.sha256(padded), ENTRIES[action], specs)
    shutil.move(str(golden), str(case / 'fixture-data'))
    shutil.rmtree(temporary)
    if from_producer:
        return {'predecessor': source_case, 'actual_input_sha256': predecessor_digest.hexdigest()}
    return {}
