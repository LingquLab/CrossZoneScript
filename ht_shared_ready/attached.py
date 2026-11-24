"""Run attached reduction with the saved, checked local gather data."""
import io
import tempfile

REVISION = '2e2264681084b1ab6f801e3c632d9d3d422183a8'
STEM = 'ht-shared-ready-t4-2e226468'
ENTRY = 'tilexr_moonep_combine_ht_attached'

def attached_case(request):
    case = MOUNT / (STEM + '-attached')
    if case.exists():
        return summarize(case)
    source = MOUNT / 'ht-four-stage-8882b28a-local-send'
    golden = MOUNT / 'ht-four-stage-8882b28a-local-reduce'
    for folder in (source, golden):
        assert json.loads((folder/'run.json').read_text())['status'] == 'sst_and_checker_passed'
    sys.path.insert(0, str(MOUNT.parent/BUILD/'source/tools/unitest/simulator/experiments/inter_server_send/sst'))
    import loader
    import native

    old = json.loads((source/'workload.yml').read_text())['workload_config']
    abis = json.loads(gzip.decompress(base64.b64decode(request['abis'], validate=True)))
    object_data = gzip.decompress(base64.b64decode(request['object'], validate=True))
    digest = hashlib.sha256(object_data).hexdigest()
    assert digest == request['sha256']
    root = '/work/inter-server-native/' + case.name
    identity = dict(tokens=4, hidden=128, chunks=4, phase=2, entry=ENTRY, runtime_root=root)
    with tempfile.TemporaryDirectory(prefix=STEM, dir=MOUNT.parent) as path:
        temp = Path(path)
        kernel, loader_output, check = temp/'kernel.o', temp/'loader', temp/'check'
        kernel.write_bytes(object_data)
        check.mkdir()
        write_json(check/'manifest.json', identity)
        ranks = []
        for rank, abi in enumerate(abis):
            chip = old[f'chip{rank}']
            parameters = chip['kernel_param']
            name = f'rank{rank:02d}'
            inputs, actual = source/'input_bin'/name, source/'actual'/name
            values = native.config_values(abi)
            bound_args, workspace, ranges, masks, exports = build_attached(
                (inputs/'kernel_args.bin').read_bytes(), (inputs/'forward.bin').read_bytes(),
                (inputs/'input.bin').read_bytes(), (actual/'returned.bin').read_bytes(),
                (actual/'completed.bin').read_bytes(), values)
            fields = [dict(name=f'word{index}', offset=index*8, type='uint64_t', value=value)
                      for index, (value,) in enumerate(struct.iter_unpack('<Q', bound_args))]
            data = dict(workspace=workspace, forward=(inputs/'forward.bin').read_bytes(),
                        ranges=ranges, masks=masks, status=b'\xa5'*512)
            positions = {field: parameters['load_'+field]['value'] for field in data}
            region = [dict(name=field, addr=positions[field], data=value) for field, value in data.items()]
            guards = []
            for label, position in (('guard_before', positions['workspace']-512),
                                    ('guard_after', positions['workspace']+len(workspace))):
                guards.append(dict(name=label, addr=position, size=512, expected_byte=222))
                region.append(dict(name=label, addr=position, data=b'\xde'*512))
            exports['status'] = (positions['status'], 512)
            ranks.append(dict(rank=rank, abi_size=len(bound_args), fields=fields, images=region, guards=guards,
                              exports={field:dict(addr=addr,size=size) for field,(addr,size) in exports.items()},
                              binary_addr=chip['bin_param']['addr'],
                              **{field:chip[field] for field in ('gm_size','sys_va_base_addr','ub_base_addr',
                                                               'stack_base_addr','param_base_addr')}))
            reference = check/name
            reference.mkdir()
            flags = struct.pack('<qii', values['magic'], 4, 0).ljust(512, b'\0') * 8
            (reference/'golden-completed.bin').write_bytes(flags)
            shutil.copyfile(actual/'returned.bin', reference/'golden-returned.bin')
            shutil.copyfile(golden/'fixture-data'/name/'golden-output.bin', reference/'golden-output.bin')
        loader.prepare_loader(loader_output, root, kernel, digest, ENTRY, ranks)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz', compresslevel=1) as archive:
            for item in check.rglob('*'):
                if item.is_file():
                    archive.add(item, arcname='fixture-data/'+str(item.relative_to(check)), recursive=False)
            for item in (loader_output/'input_bin').rglob('*.bin'):
                archive.add(item, arcname=str(item.relative_to(loader_output)), recursive=False)
            for name in ('workload.yml','export-layout.json'):
                archive.add(loader_output/name, arcname=name, recursive=False)
        fixture = buffer.getvalue()
        prepare_bulk(dict(tokens=4, hidden=128, archive=base64.b64encode(fixture).decode(),
                          identity=dict(identity, input_archive_sha256=hashlib.sha256(fixture).hexdigest()),
                          object=request['object'], checker=request['checker'], exports=request['exports'],
                          build=dict(entry=ENTRY, variant=case.name, candidate_commit=REVISION,
                                     kernel_sha256=digest, compiled_in='blue')), case, root)
    return execute_phase(case)
