"""Prepare each SST phase and pass verified device exports to its consumer."""
import io
import tempfile
import zipfile

REVISION = '8882b28a4cc586f91ddcea171170bf9e304adc70'
STEM = 'ht-bs64-random-20261124-'
INTER_STEM = ''
ENTRIES = (NATIVE, NATIVE, 'tilexr_ep_ht_inter_server_send_kernel', 'tilexr_ep_ht_final_reduce_sst_kernel')
SENDER_HASH = '5851c89daf6e400813e2c963841d4cf1bdf6690c06ddb745999d4e566908181d'
FINAL_HASH = '561e4bcb78c036a2bde27bb19f8e38e18e72b2488c691500151cd6eba90037d5'
EXTRA = {'input': 0x404000000, 'prefill': 0x405000000, 'masks': 0x406000000,
         'serverMask': 0x404000000, 'localReady': 0x405000000, 'output': 0x406000000}


def slot_offset(token, server):
    return (token * (WORLD // LOCAL) + server) * ROW_BYTES


def stage_directory(phase):
    stem = INTER_STEM if phase >= 3 and INTER_STEM else STEM
    return MOUNT / (stem + ('local-send', 'local-reduce', 'inter-send', 'final-reduce')[phase - 1])


def read_stage(phase, rank, field, expected_bytes):
    source = stage_directory(phase) / 'actual' / f'rank{rank:02d}' / (field + '.bin')
    data = source.read_bytes()
    assert len(data) == expected_bytes, (phase, rank, field, len(data), expected_bytes)
    return data


def rows_bytes(tags, capacity):
    assert len(tags) <= capacity
    return b''.join(floats(row(tag)) for tag in tags).ljust(capacity * ROW_BYTES, b'\0')


def local_rank(phase, rank, abi, plans, routes, runtime):
    inputs, gather, _, returns, _, _ = routes
    raw = bytearray(plans.read(f'{rank}/args.bin'))
    config = struct.unpack_from('<13I', raw, 112)
    assert config[:5] == (rank, WORLD, TOKENS, HIDDEN, TOPK) and config[11:] == (3, 0)
    input_rows, gather_rows = config[5:7]
    chunks, groups, _, capacity, _, _, goff, poff, wbytes, _ = struct.unpack_from('<6I4Q', raw, 168)
    assert (chunks, groups, capacity) == (CHUNKS, 1, TOKENS * TOPK) and len(raw) == 248
    base = rank * runtime.GM_BYTES
    addresses = runtime.BASES | EXTRA
    images = runtime.runtime_images(abi, rank, wbytes)
    images['input'] = rows_bytes(inputs[rank], input_rows)
    images['forward'], images['ranges'], images['masks'], _ = local_tables(routes, rank, capacity)
    if phase == 1:
        images['prefill'] = plans.read(f'{rank}/prefill.bin')
    images['status'] = b'\xa5' * 512
    done_offset = (3 * WORLD + 96) * ROW_BYTES
    done_bytes = (chunks * LOCAL + LOCAL) * ROW_BYTES
    workspace = bytearray(wbytes)
    for gateway in range(LOCAL):
        struct.pack_into('<Q', workspace, (3 * WORLD + gateway) * ROW_BYTES, 1)
    returned = rows_bytes(gather[rank], gather_rows)
    completed = bytearray(done_bytes)
    for chunk, mask in enumerate(struct.unpack('<4i', images['masks'])):
        for sender in range(LOCAL):
            if mask & (1 << sender):
                struct.pack_into('<Q', completed, (chunk * LOCAL + sender) * ROW_BYTES, 1)
    output = bytearray(input_rows * ROW_BYTES)
    if phase == 2:
        workspace[goff:goff + len(returned)] = read_stage(1, rank, 'returned', len(returned))
        workspace[done_offset:done_offset + done_bytes] = read_stage(1, rank, 'completed', done_bytes)
        for reducer in range(LOCAL):
            struct.pack_into('<I', completed, (chunks * LOCAL + reducer) * ROW_BYTES, chunks)
        for _, _, primary, tags in returns[rank]:
            if len(tags) > 1:
                output[primary * ROW_BYTES:(primary + 1) * ROW_BYTES] = add_rows(tags)
    images['workspace'] = workspace
    for offset, key in ((0, 'workspace'), (8, 'input'), (16, 'forward'), (32, 'ranges'), (40, 'masks'),
                        (72, 'status'), (88, 'commArgs'), (96, 'udma_info'), (104, 'registry'), (240, 'prefill')):
        if key in images:
            struct.pack_into('<Q', raw, offset, base + addresses[key])
    struct.pack_into('<I', raw, 156, phase)
    fields = [dict(name=f'word{index}', offset=index * 8, type='uint64_t', value=value)
              for index, (value,) in enumerate(struct.iter_unpack('<Q', raw))]
    golden = dict(returned=returned, completed=completed, output=output)
    exports = dict(returned=('workspace', goff, len(returned)), completed=('workspace', done_offset, len(completed)),
                   output=('workspace', poff, len(output)), status=('status', 0, 512))
    return fields, images, golden, exports


def remote_rank(phase, rank, abi, routes, incoming, partial_cache, runtime, native):
    inputs, _, _, returns, _, original = routes
    c = native.config_values(abi)
    base = rank * runtime.GM_BYTES
    addresses = runtime.BASES | EXTRA
    location = {name: base + off for name, off in addresses.items()}
    images = runtime.runtime_images(abi, rank, c['workspaceBytes'])
    ws = bytearray(c['workspaceBytes'])
    rows = c['inputRows']
    local_input_bytes = rows_bytes(inputs[rank], rows)
    ws[c['inputOffset']:c['inputOffset'] + len(local_input_bytes)] = local_input_bytes
    assert len(partial_cache[rank]) == rows * ROW_BYTES
    ws[c['partialOffset']:c['partialOffset'] + rows * ROW_BYTES] = partial_cache[rank]
    for reducer in range(c['reducerCount']):
        struct.pack_into('<qq', ws, c['readyOffset'] + reducer * ROW_BYTES, MAGIC, CHUNKS)
    ret_len = c['signalOffset'] - c['returnOffset']
    cmp_len = c['returnOffset'] - c['completionOffset']
    assert ret_len == TOKENS * (WORLD // LOCAL) * ROW_BYTES
    returned, completed = bytearray(ret_len), bytearray(cmp_len)
    for gateway in range(WORLD):
        if gateway // LOCAL != rank // LOCAL:
            for lane in range(2):
                offset = ((gateway * CHUNKS + CHUNKS - 1) * 2 + lane) * ROW_BYTES
                struct.pack_into('<q', completed, offset, MAGIC)
    masks = [0] * TOKENS
    for gateway, token, primary, tags in incoming[rank]:
        server = gateway // LOCAL
        masks[token] |= 1 << server
        if phase == 4 or server != rank // LOCAL:
            offset = slot_offset(token, server)
            returned[offset:offset + ROW_BYTES] = add_rows(tags)
    output = struct.pack('<I', 0x7fc00000) * (TOKENS * HIDDEN)
    local_ready = bytearray(LOCAL * ROW_BYTES)
    if phase == 4:
        actual = bytearray(read_stage(3, rank, 'returned', ret_len))
        for gateway, token, primary, tags in incoming[rank]:
            if gateway // LOCAL == rank // LOCAL:
                offset = slot_offset(token, gateway // LOCAL)
                assert not any(actual[offset:offset + ROW_BYTES])
                data = (partial_cache[gateway][primary * ROW_BYTES:(primary + 1) * ROW_BYTES] if len(tags) > 1
                        else floats(row(inputs[gateway][primary])))
                actual[offset:offset + ROW_BYTES] = data
        ws[c['returnOffset']:c['signalOffset']] = actual
        ws[c['completionOffset']:c['returnOffset']] = read_stage(3, rank, 'completed', cmp_len)
        for gateway in range(LOCAL):
            struct.pack_into('<q', local_ready, gateway * ROW_BYTES, MAGIC)
        fields = [dict(name=name, offset=index * 8, value=location[name], type='GM_ADDR')
                  for index, name in enumerate(('workspace', 'serverMask', 'localReady', 'output', 'status'))]
        for item in abi['fields'][6:]:
            field = dict(item, offset=item['offset'] - 8)
            if field['name'] == 'config.hidden':
                field['value'] = HIDDEN
            fields.append(field)
    else:
        fields, _, _ = native.native_arguments(abi, {key: location[key] for key in native.POINTERS})
    records = bytearray(WORLD * c['forwardCapacity'] * 32)
    ranges, counts = [0] * (WORLD * CHUNKS * 2), [0] * WORLD
    if phase == 3:
        per_peer = defaultdict(list)
        for peer, token, primary, tags in returns[rank]:
            if peer // LOCAL != rank // LOCAL:
                per_peer[peer].append((token, primary, tags))
        for peer, values in per_peer.items():
            counts[peer] = len(values)
            cursor = 0
            for chunk in range(CHUNKS):
                begin = cursor
                for token, primary, tags in values:
                    if token // 16 == chunk:
                        off = (peer * c['forwardCapacity'] + cursor) * 32
                        struct.pack_into('<6i', records, off, cursor + 1, token, primary,
                                         -1 if len(tags) == 1 else 0, primary, 0)
                        cursor += 1
                index = (peer * CHUNKS + chunk) * 2
                ranges[index:index + 2] = begin, cursor
            assert cursor == counts[peer]
    images.update(workspace=ws, forward=records, counts=struct.pack('<64i', *counts),
                  ranges=struct.pack('<512i', *ranges), status=native.status_image(), localReady=local_ready,
                  serverMask=struct.pack('<64Q', *masks), output=output)
    golden_output = b''.join(add_rows(tags) for tags in original[rank]) if phase == 4 else output
    golden = dict(returned=returned, completed=completed, output=golden_output)
    exports = dict(returned=('workspace', c['returnOffset'], ret_len), completed=('workspace', c['completionOffset'], cmp_len),
                   output=('output', 0, len(output)), status=('status', 0, 512))
    return fields, images, golden, exports


def rank_case(rank, fields, images, exports, runtime):
    base = rank * runtime.GM_BYTES
    addresses = runtime.BASES | EXTRA
    guards = []
    memory = [dict(name=name, addr=base + addresses[name], data=value) for name, value in images.items()]
    for name in ('workspace', 'output'):
        if name not in images:
            continue
        for suffix, offset in (('before', -ROW_BYTES), ('after', len(images[name]))):
            address = base + addresses[name] + offset
            guard = f'guard_{name}_{suffix}'
            memory.append(dict(name=guard, addr=address, data=b'\xde' * ROW_BYTES))
            guards.append(dict(name=guard, addr=address, size=ROW_BYTES, expected_byte=222))
    export = {field: dict(addr=base + addresses[name] + offset, size=size)
              for field, (name, offset, size) in exports.items()}
    abi_size = max(field['offset'] + (4 if field['type'] == 'uint32_t' else 8) for field in fields)
    return dict(rank=rank, abi_size=abi_size, fields=fields, gm_size=runtime.GM_BYTES, sys_va_base_addr=base,
                ub_base_addr=0x80000, stack_base_addr=0x100000, param_base_addr=base+0x80000000,
                binary_addr=base+0x40000000, images=memory, exports=export, guards=guards)


def phase_object(phase, request, temp):
    build = MOUNT.parent / BUILD
    if phase == 1 and 'guarded_local' in request:
        data = gzip.decompress(base64.b64decode(request['guarded_local'], validate=True))
        expected = '3a781340ce77e377a71629518c5d619df03eed648eac9fad089df645a8326344'
    elif phase <= 2:
        data = (build / 'sst/src/moonep/combine_ht' / (NATIVE + '.o')).read_bytes()
        assert hashlib.sha256(data).hexdigest() == EXPECTED
        data = data.ljust(10485760, b'\0')
        expected = PADDED
    elif phase == 3:
        if 'guarded_inter' in request:
            data = gzip.decompress(base64.b64decode(request['guarded_inter'], validate=True))
            expected = '504b7253bd2ad37115026a06bdbbffc240266052afa8877b900c11a135f2cfa9'
        else:
            data = gzip.decompress(base64.b64decode(request['sender'], validate=True))
            expected = SENDER_HASH
    else:
        data = (build / 'sst/src/ep' / (ENTRIES[3] + '.o')).read_bytes()
        expected = FINAL_HASH
    assert hashlib.sha256(data).hexdigest() == expected
    path = temp / 'kernel.o'
    path.write_bytes(data)
    return path, expected


def prepare_phase(phase, request, temp, modules):
    loader, native, runtime = modules
    case = stage_directory(phase)
    runtime_root = '/work/inter-server-native/' + case.name
    object_path, digest = phase_object(phase, request, temp)
    entry = ENTRIES[phase - 1]
    identity = dict(tokens=TOKENS, hidden=HIDDEN, topk=TOPK, experts=EXPERTS, seed=SEED, chunks=CHUNKS,
                    world=WORLD, entry=entry, kernel_sha256=digest, runtime_root=runtime_root, phase=phase)
    routes = build_routes()
    plans = zipfile.ZipFile(io.BytesIO(base64.b64decode(request['plans'], validate=True)))
    golden_directory = temp / 'golden'
    golden_directory.mkdir()
    ranks = []
    incoming = [[] for _ in range(WORLD)]
    partial_cache = []
    if phase >= 3:
        for gateway, returns in enumerate(routes[3]):
            for source, token, primary, tags in returns:
                incoming[source].append((gateway, token, primary, tags))
        for rank in range(WORLD):
            layout = struct.unpack_from('<6I4Q', plans.read(f'{rank}/args.bin'), 168)
            progress = read_stage(2, rank, 'completed', (CHUNKS * LOCAL + LOCAL) * ROW_BYTES)
            assert all(struct.unpack_from('<I', progress, index * ROW_BYTES)[0] == CHUNKS
                       for index in range(CHUNKS * LOCAL, CHUNKS * LOCAL + LOCAL))
            partial_cache.append(read_stage(2, rank, 'output', layout[8] - layout[7]))
    for rank in range(WORLD):
        abi = json.loads(plans.read(f'{rank}/abi.json'))
        if phase <= 2:
            spec = local_rank(phase, rank, abi, plans, routes, runtime)
        else:
            spec = remote_rank(phase, rank, abi, routes, incoming, partial_cache, runtime, native)
        fields, images, golden, exports = spec
        folder = golden_directory / f'rank{rank:02d}'
        folder.mkdir()
        for name, data in golden.items():
            (folder / f'golden-{name}.bin').write_bytes(data)
        ranks.append(rank_case(rank, fields, images, exports, runtime))
    write_json(golden_directory / 'manifest.json', identity)
    generated = temp / 'loader'
    loader.prepare_loader(generated, runtime_root, object_path, digest, entry, ranks)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz', compresslevel=1) as archive:
        for item in sorted(golden_directory.rglob('*.bin')) + [golden_directory / 'manifest.json']:
            archive.add(item, arcname='fixture-data/' + str(item.relative_to(golden_directory)), recursive=False)
        for item in sorted((generated / 'input_bin').rglob('*.bin')):
            archive.add(item, arcname=str(item.relative_to(generated)), recursive=False)
        for item in ('workload.yml', 'export-layout.json'):
            archive.add(generated / item, arcname=item, recursive=False)
    data = buffer.getvalue()
    prepare_bulk(dict(tokens=TOKENS, hidden=HIDDEN, archive=base64.b64encode(data).decode(),
                      identity=dict(identity, input_archive_sha256=hashlib.sha256(data).hexdigest()),
                      object=base64.b64encode(gzip.compress(object_path.read_bytes())).decode(),
                      checker=request['checker'], exports=request['exports'],
                      build=dict(entry=entry, variant=case.name, candidate_commit=REVISION,
                                 kernel_sha256=digest, compiled_in='retained')), case, runtime_root)


def execute_request(request):
    global STEM, INTER_STEM
    if 'guarded_local' in request or 'guarded_inter' in request:
        STEM = 'ht-bs64-random-20261124-guarded-'
    if 'guarded_inter' in request:
        INTER_STEM = 'ht-bs64-random-20261124-guarded-inter-'
        assert all(summarize(stage_directory(phase)).get('status') == 'sst_and_checker_passed'
                   for phase in (1, 2))
    support = MOUNT.parent / BUILD / 'source/tools/unitest/simulator/experiments/inter_server_send/sst'
    sys.path.insert(0, str(support))
    import loader
    import native
    import runtime

    results = []
    for phase in range(1, 5):
        case = stage_directory(phase)
        if not case.exists():
            with tempfile.TemporaryDirectory(prefix=STEM, dir=MOUNT.parent) as temp:
                prepare_phase(phase, request, Path(temp), (loader, native, runtime))
            result = execute_phase(case)
        else:
            result = summarize(case)
        result.pop('exports_sha256', None)
        results.append(result)
        if result.get('status') != 'sst_and_checker_passed':
            break
    return dict(phases=results, seed=SEED, tokens=TOKENS, hidden=HIDDEN, topk=TOPK, experts=EXPERTS)
