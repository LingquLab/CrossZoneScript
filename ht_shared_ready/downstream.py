"""Consume actual attached exports in the two return kernels and final reduce."""

STAGES = {
    'inter_server_send': ('tilexr_ep_ht_inter_server_send_kernel', 16),
    'local_return': ('tilexr_ep_ht_local_return_kernel', 8),
    'final_reduce': ('tilexr_ep_ht_final_reduce_sst_kernel', 16),
}
WORLD = 64
EXTRA = {'output': 0x406000000, 'serverMask': 0x404000000}
OUTPUT_BYTES = TOKENS * ROW_BYTES
CASE_SUFFIX = {}

def stage_path(stage):
    return MOUNT / (STEM + '-' + stage + CASE_SUFFIX.get(stage, ''))


def read_export(stage, rank, field):
    case = stage_path(stage)
    return (case/'actual'/f'rank{rank:02d}'/(field+'.bin')).read_bytes()


def stage_checked(stage):
    case = stage_path(stage)
    return json.loads((case/'run.json').read_text())['status'] == 'sst_and_checker_passed'


def golden_partial(server, token):
    return [sum(server * 64 + peer * 8 + token + h / 64 for peer in range(LOCAL))
            for h in range(128)]


def expected_values(stage, rank, config):
    local_server = rank // LOCAL
    remote_server = (rank ^ LOCAL) // LOCAL
    returned = bytearray(config['signalOffset'] - config['returnOffset'])
    completed = bytearray(config['returnOffset'] - config['completionOffset'])
    local_ready = b''
    output = bytearray(OUTPUT_BYTES)
    for token in range(TOKENS):
        for server, enabled in ((local_server, stage != 'inter_server_send'),
                                (remote_server, stage != 'local_return')):
            if enabled:
                struct.pack_into('<128f', returned, (token * LOCAL + server) * ROW_BYTES,
                                 *golden_partial(server, token))
        if stage == 'final_reduce':
            total = [a+b for a,b in zip(golden_partial(local_server, token), golden_partial(remote_server, token))]
            struct.pack_into('<128f', output, token*ROW_BYTES, *total)
    if stage != 'local_return':
        for gateway in range(WORLD):
            if gateway // LOCAL != local_server:
                for lane in range(2):
                    slot = (gateway*TOKENS + TOKENS-1)*2+lane
                    struct.pack_into('<q', completed, slot*ROW_BYTES, config['magic'])
    else:
        local_ready = struct.pack('<q', config['magic']).ljust(ROW_BYTES, b'\0') * LOCAL
    return dict(returned=returned, completed=local_ready or completed, output=output)


def rank_binding(stage, rank, abi, native, runtime):
    c = native.config_values(abi)
    assert c['rank'] == rank
    ready = read_export('attached', rank, 'completed')
    partial = read_export('attached', rank, 'output')
    sources = ()
    if stage == 'final_reduce':
        sources = (read_export('inter_server_send', rank, 'returned'),
                   read_export('inter_server_send', rank, 'completed'),
                   read_export('local_return', rank, 'returned'),
                   read_export('local_return', rank, 'completed'))
    workspace, forward, counts, ranges = build_returns(c, partial, ready, *sources)
    if stage == 'local_return' and CASE_SUFFIX.get(stage) == '-packed24':
        forward = b''.join(forward[offset:offset+24] for offset in range(0, len(forward), 32))
    base = rank * runtime.GM_BYTES
    addresses = {name:base+offset for name,offset in (runtime.BASES | EXTRA).items()}
    blocks = STAGES[stage][1]
    images = runtime.runtime_images(abi, rank, len(workspace))
    images.update(workspace=workspace, forward=forward, counts=counts, ranges=ranges,
                  status=(b'\xff'*4+b'\xa5'*28)*blocks, output=bytes(OUTPUT_BYTES))
    if stage == 'inter_server_send':
        fields, _, _ = native.native_arguments(abi, {name:addresses[name] for name in native.POINTERS})
    else:
        pointer_names = (('udma_info','registry','workspace','forward','counts','ranges','status')
                         if stage == 'local_return' else ('workspace','serverMask','localReady','output','status'))
        if stage == 'final_reduce':
            images['serverMask'] = struct.pack('<Q', ((1 << (rank//LOCAL)) | (1 << ((rank^LOCAL)//LOCAL))))*TOKENS
            addresses['localReady'] = addresses['workspace'] + c['workspaceBytes']
        fields = [dict(name=name,type='GM_ADDR',offset=i*8,value=addresses[name])
                  for i,name in enumerate(pointer_names)]
        for field in abi['fields'][6:]:
            field = dict(field, offset=field['offset']+(len(pointer_names)-6)*8)
            if stage == 'final_reduce' and field['name'] == 'config.hidden':
                field['value'] = 128
            fields.append(field)
    golden = expected_values(stage, rank, c)
    work = addresses['workspace']
    completed_offset = c['workspaceBytes'] if stage == 'local_return' else c['completionOffset']
    exports = {field:dict(addr=address,size=size) for field,address,size in (
        ('returned', work+c['returnOffset'], len(golden['returned'])),
        ('completed', work+completed_offset, len(golden['completed'])),
        ('output',addresses['output'],OUTPUT_BYTES),
        ('status',addresses['status'],blocks*32))}
    memory = [dict(name=name,addr=addresses[name],data=image) for name,image in images.items()]
    guards = []
    for field in ('workspace','output'):
        for suffix, offset in (('before',-ROW_BYTES),('after',len(images[field]))):
            name, address = f'guard_{field}_{suffix}', addresses[field]+offset
            guards.append(dict(name=name,addr=address,size=ROW_BYTES,expected_byte=222))
            memory.append(dict(name=name,addr=address,data=b'\xde'*ROW_BYTES))
    return dict(rank=rank, abi_size=fields[-1]['offset']+8, fields=fields, images=memory, exports=exports,
                guards=guards, gm_size=runtime.GM_BYTES, sys_va_base_addr=base, ub_base_addr=0x80000,
                stack_base_addr=0x100000,param_base_addr=base+0x80000000,binary_addr=base+0x40000000), golden


def prepare_stage(request, stage, case, temp, loader, native, runtime, abis):
    entry, blocks = STAGES[stage]
    encoded = request['objects'][stage]
    data = gzip.decompress(base64.b64decode(encoded, validate=True))
    digest = hashlib.sha256(data).hexdigest()
    assert digest == request['hashes'][stage]
    kernel = temp/'kernel.o'
    kernel.write_bytes(data)
    root = '/work/inter-server-native/' + case.name
    identity = dict(tokens=TOKENS,hidden=128,chunks=TOKENS,phase=3,entry=entry,runtime_root=root)
    golden = temp/'golden'
    golden.mkdir()
    write_json(golden/'manifest.json',identity)
    ranks = []
    for rank, abi in enumerate(abis):
        binding, expected = rank_binding(stage,rank,abi,native,runtime)
        directory = golden/f'rank{rank:02d}'
        directory.mkdir()
        for field,value in expected.items():
            (directory/f'golden-{field}.bin').write_bytes(value)
        ranks.append(binding)
    generated = temp/'loader'
    loader.prepare_loader(generated,root,kernel,digest,entry,ranks,blocks=blocks)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz',compresslevel=1) as archive:
        for item in golden.rglob('*'):
            if item.is_file():
                archive.add(item,arcname='fixture-data/'+str(item.relative_to(golden)),recursive=False)
        for item in (generated/'input_bin').rglob('*.bin'):
            archive.add(item,arcname=str(item.relative_to(generated)),recursive=False)
        for name in ('workload.yml','export-layout.json'):
            archive.add(generated/name,arcname=name,recursive=False)
    fixture = buffer.getvalue()
    checker = request['checker'].replace('* 16 if field == "status"', f'* {blocks} if field == "status"')
    prepare_bulk(dict(tokens=TOKENS,hidden=128,archive=base64.b64encode(fixture).decode(),object=encoded,
                      identity=dict(identity,input_archive_sha256=hashlib.sha256(fixture).hexdigest()),
                      checker=checker,exports=request['exports'],
                      build=dict(entry=entry,variant=case.name,candidate_commit=REVISION,kernel_sha256=digest,
                                 compiled_in='blue')),case,root)


def run_downstream(request):
    if request.get('packed24'):
        CASE_SUFFIX.update(local_return='-packed24', final_reduce='-packed24')
    assert stage_checked('attached')
    sys.path.insert(0,str(MOUNT.parent/BUILD/'source/tools/unitest/simulator/experiments/inter_server_send/sst'))
    import native
    import runtime
    loader = type(sys)('loader')
    exec(compile(request['loader'], '<fixture-loader-at-2e226468>', 'exec'), loader.__dict__)
    abis = json.loads(gzip.decompress(base64.b64decode(request['abis'], validate=True)))
    results = []
    for stage in request['stages']:
        assert stage in STAGES
        if stage == 'final_reduce':
            assert stage_checked('inter_server_send') and stage_checked('local_return')
        case = stage_path(stage)
        if case.exists():
            result = summarize(case)
        else:
            with tempfile.TemporaryDirectory(prefix=STEM,dir=MOUNT.parent) as directory:
                prepare_stage(request,stage,case,Path(directory),loader,native,runtime,abis)
            result = execute_phase(case)
        if stage == 'local_return' and result.get('status') != 'sst_and_checker_passed':
            result['diagnostics'] = read_local_evidence(case)
        results.append(result)
        if result.get('status') != 'sst_and_checker_passed':
            break
    return dict(phases=results)
