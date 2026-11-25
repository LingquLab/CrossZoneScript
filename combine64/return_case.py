# Copyright (c) 2026 TileXR Project.
"""Prepare registered buffers for independent and joint HT return cases."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

from fixture import DiskImages, GUARD
from sim_case import BLOCKS, ENTRIES, FINAL_CORES, MAGIC, REDUCERS, REVISION, SOURCE, STRIPE, SUPPORT, WORLD

LOCAL_WS, RETURN_WS = 0x200000000, 0x220000000
LOCAL_RESOURCES = {
    'localComm': 0x2FF100000, 'localInfo': 0x300080000, 'localRegistry': 0x3000A0000,
    'localSq': 0x317000000, 'localCq': 0x318000000, 'localMem': 0x319000000,
}
EXTRA = {
    'localWorkspace': 0x470000000, 'localStatus': 0x40B000000, 'serverMask': 0x40C000000,
    'returnForward': 0x40D000000, 'output': 0x40E000000, 'prefill': 0x405000000,
    'masks': 0x406000000, 'backward': 0x407000000, 'backward_counts': 0x408000000,
    'backward_ranges': 0x409000000,
}


def local_slot(qp):
    return (qp % 2) * 20 + 16 + qp // 2


def read_json(path):
    return json.loads(path.read_text())


def send_config(abi):
    fields = [dict(field, name=field['name'].split('.', 1)[1], offset=field['offset'] - 48)
              for field in abi['fields'][6:]]
    from loader import pack_arguments
    return {field['name']: field['value'] for field in fields}, pack_arguments(fields, 112)[0]


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


def local_communication(runtime, images, abi, local_bytes, base):
    control = abi['runtime_abi']
    sq, cq, memory = bytearray(), bytearray(), bytearray()
    helper_mem = bytearray(WORLD * 32 * control['mem_bytes'])
    model_memory = bytearray(images['mem_info'])
    for peer in range(WORLD):
        for qp in range(8):
            slot = peer * 40 + local_slot(qp)
            width = control['mem_bytes']
            at = slot * width
            entry = model_memory[at:at + width]
            struct.pack_into('<I', entry, 16, (local_bytes + 2097151) // 2097152 * 2097152)
            struct.pack_into('<Q', entry, 24, peer * runtime.GM_BYTES + LOCAL_WS)
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
        address = peer * runtime.GM_BYTES + LOCAL_WS
        struct.pack_into('<Q', comm, control['comm_peer_mems'] + peer * 8, address)
        struct.pack_into('<QQ', registry, control['registry_regions'] + peer * 16, address, local_bytes)
    struct.pack_into('<Q', comm, control['comm_peer_bytes'], local_bytes)
    struct.pack_into('<Q', comm, control['comm_udma_info'], base + LOCAL_RESOURCES['localInfo'])
    struct.pack_into('<Q', comm, control['comm_udma_registry'], base + LOCAL_RESOURCES['localRegistry'])
    info = bytearray(images['udma_info'])
    struct.pack_into('<I', info, 0, 8)
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


def prepare_return_case(case, asset, action, binary, source_case):
    sys.path.insert(0, str(SUPPORT))
    import loader
    import runtime

    joint = action.startswith('joint-')
    producer = action == 'joint-producer'
    final = action.startswith('final-')
    local = action == 'local-return'
    remote = action == 'inter-server-send'
    from_producer = action == 'final-from-producer'
    tokens = read_json(asset / 'identity.json')['tokens']
    routes = read_json(asset / 'routes.json')
    assert len(routes) == WORLD * tokens
    if from_producer:
        assert source_case and Path(source_case).name == source_case
        predecessor = case.parent / source_case
        previous = read_json(predecessor / 'phase-result.json')
        assert previous['status'] == 'sst_and_checker_passed'
        assert (previous['candidate_commit'], previous['stage'], previous['tokens']) == (REVISION, 'joint-producer', tokens)
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
            str(MAGIC), '-', '64', str(REDUCERS),
        ]))
        cfg, config_bytes = send_config(abi)
        assert (cfg['rank'], cfg['chunks'], cfg['reducerCount'], abi['forward_record_words']) == (rank, 4, REDUCERS, 8)
        base = rank * runtime.GM_BYTES
        addresses = dict(runtime.BASES, **EXTRA)
        local_ready = cfg['workspaceBytes']
        return_bytes = local_ready + (8 + WORLD) * STRIPE
        ret_address = RETURN_WS if joint else runtime.BASES['workspace']
        if joint:
            addresses.update(LOCAL_RESOURCES, returnWorkspace=RETURN_WS)
            runtime.BASES['workspace'] = RETURN_WS
        try:
            images = runtime.runtime_images(abi, rank, return_bytes, world=WORLD)
        finally:
            runtime.BASES['workspace'] = LOCAL_WS
        initial = bytearray(return_bytes)
        inp = (local_input / 'input.bin').read_bytes()
        partial = (local_golden / 'golden-output.bin').read_bytes()
        assert len(inp) == len(partial) == rows * STRIPE
        initial[:len(inp)] = inp
        initial_partial = GUARD * (len(partial) // 4)
        initial[cfg['partialOffset']:cfg['readyOffset']] = initial_partial if joint else partial
        returned, mask, final_output = station_gold(routes, rank, tokens, local)
        initial_returned = GUARD * (len(returned) // 4)
        initial[cfg['returnOffset']:cfg['signalOffset']] = initial_returned
        completion = bytearray(cfg['returnOffset'] - cfg['readyOffset'])
        for reducer in range(REDUCERS):
            struct.pack_into('<qq', completion, reducer * STRIPE, MAGIC, cfg['chunks'])
        if not local:
            for gateway in range(WORLD):
                if gateway // 8 != rank // 8:
                    slot = REDUCERS + (gateway * cfg['chunks'] + cfg['chunks'] - 1) * 2
                    for lane in range(2):
                        struct.pack_into('<q', completion, (slot + lane) * STRIPE, MAGIC)
        flags = bytearray(8 * STRIPE)
        for gateway in range(8):
            struct.pack_into('<q', flags, gateway * STRIPE, MAGIC)
        if remote:
            initial[cfg['readyOffset']:cfg['completionOffset']] = completion[:REDUCERS * STRIPE]
            for token in range(tokens):
                start = (token * 8 + rank // 8) * STRIPE
                initial[cfg['returnOffset'] + start:cfg['returnOffset'] + start + STRIPE] = returned[start:start + STRIPE]
            initial[local_ready:local_ready + len(flags)] = flags
        if final:
            for field, offset, reference in (('returned', cfg['returnOffset'], returned),
                                              ('completed', cfg['readyOffset'], completion),
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
        images['returnWorkspace' if joint else 'workspace'] = initial
        for name in ('counts', 'ranges'):
            images[name] = (local_input / (name + '.bin')).read_bytes()
        forward = (local_input / 'forward.bin').read_bytes()
        return_forward = b''.join(struct.pack('<8i', *row, 0, 0) for row in struct.iter_unpack('<6i', forward))
        images['returnForward' if joint else 'forward'] = return_forward
        pointer = lambda name: base + addresses[name]
        export('returned', ret_address + cfg['returnOffset'], returned)
        export('completed', ret_address + cfg['readyOffset'], completion)
        export('local_ready', ret_address + local_ready, flags)
        regions = {'returnWorkspace' if joint else 'workspace': return_bytes}
        out_status = None if joint else success_status(512, BLOCKS[action])
        args = None
        if joint:
            local_ws = (local_input / 'workspace.bin').read_bytes()
            helper_memory = local_communication(runtime, images, abi, len(local_ws), base)
            old_args = (local_input / 'kernel_args.bin').read_bytes()
            helper_file = temporary / rank_name
            config = bytearray(old_args[112:164])
            struct.pack_into('<I', config, 36, 24)
            Path(str(helper_file) + '.config').write_bytes(config)
            Path(str(helper_file) + '.native_mem_info').write_bytes(helper_memory)
            for name in ('masks', 'backward', 'backward_counts', 'backward_ranges'):
                images[name] = (local_input / (name + '.bin')).read_bytes()
                if name in ('backward', 'backward_counts'):
                    Path(str(helper_file) + '.' + name).write_bytes(images[name])
            subprocess.run([str(asset / 'prefill-tool'), str(helper_file), str(RETURN_WS)], check=True)
            images.update(workspace=local_ws, forward=forward, prefill=Path(str(helper_file) + '.prefill').read_bytes())
            raw_local = bytearray(Path(str(helper_file) + '.args').read_bytes())
            for offset, name in ((88, 'localComm'), (96, 'localInfo'), (104, 'localRegistry')):
                struct.pack_into('<Q', raw_local, offset, pointer(name))
            send_args = struct.pack('<6Q', pointer('commArgs'), pointer('returnWorkspace'),
                                    pointer('returnForward'), pointer('counts'), pointer('ranges'),
                                    base + status_addr + 2 * STRIPE) + config_bytes
            args = raw_local + send_args + struct.pack('<5Q2I', base + status_addr + STRIPE, pointer('serverMask'),
                pointer('output'), base + status_addr + 3 * STRIPE, local_ready, FINAL_CORES, int(not producer))
            assert len(args) == 456
            local_status = success_status(512, 8, 4)
            local_status[24 * 4:32 * 4] = bytes(8 * 4)
            out_status = local_status + success_status(512, 8) + success_status(512, 16) + success_status(512, 0 if producer else FINAL_CORES)
            gather_offset = struct.unpack_from('<Q', old_args, 192)[0]
            export('gather', LOCAL_WS + gather_offset, (local_golden / 'golden-returned.bin').read_bytes())
            export('partial', RETURN_WS + cfg['partialOffset'], partial)
            export('output', addresses['output'], images['output'] if producer else final_output)
            regions.update(workspace=len(local_ws), output=len(final_output))
        elif local:
            local_ws = bytearray((local_input / 'workspace.bin').read_bytes())
            progress_offset = (3 * WORLD + 96 + cfg['chunks'] * 8) * STRIPE
            for reducer in range(REDUCERS):
                struct.pack_into('<I', local_ws, progress_offset + reducer * STRIPE, cfg['chunks'])
            images.update(localWorkspace=local_ws, localStatus=success_status(512, 8, 4))
            args = struct.pack('<8Q', *(pointer(name) for name in ('commArgs', 'workspace', 'localWorkspace',
                'forward', 'counts', 'ranges', 'localStatus', 'status'))) + config_bytes
            args += struct.pack('<2Q2I', progress_offset, local_ready, cfg['chunks'], REDUCERS)
            assert len(args) == 200
        elif remote:
            args = struct.pack('<6Q', *(pointer(name) for name in ('commArgs', 'workspace', 'forward',
                'counts', 'ranges', 'status'))) + config_bytes
            assert len(args) == 160
        else:
            args = struct.pack('<5Q', pointer('workspace'), pointer('serverMask'), pointer('workspace') + local_ready,
                               pointer('output'), pointer('status')) + config_bytes
            assert len(args) == 152
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
        specs.append(dict(rank=rank, block_dim=BLOCKS[action], abi_size=len(args),
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
