"""Install and run each paired send-only workload on the retained SST platform."""
import io
import json
import struct
from pathlib import Path
import tempfile

from fixture import BASE, LOCAL, MAGIC, ROW_BYTES, TOPK, EXPERTS, WORLD, generate_inputs

REVISION = '8882b28a4cc586f91ddcea171170bf9e304adc70'
ENTRY = 'tilexr_ep_ht_inter_server_send_kernel'
STEM = 'ht-peer-order-v1-'
MODES = ('rotate', 'peer')


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def write_fixture(generated, golden_directory, case, mode, tokens, seed, data, abis, kernel, digest, modules):
    loader, native, runtime = modules
    inputs, expected, routes, route_sha, lanes = data
    runtime_root = '/work/inter-server-native/' + case.name
    ranks = []
    golden_directory.mkdir()
    for rank, abi in enumerate(abis):
        base = rank * runtime.GM_BYTES
        addresses = runtime.BASES | {'output': 0x406000000}
        locations = {name: base + address for name, address in addresses.items()}
        fields, _, _ = native.native_arguments(abi, {key: locations[key] for key in native.POINTERS})
        mode_field = next(field for field in fields if field['name'] == 'config.reserved')
        mode_field['value'] = int(mode == 'peer')
        config = native.config_values(abi)
        images = inputs[rank]
        done_bytes = config['returnOffset'] - config['completionOffset']
        completed = bytearray(done_bytes)
        for gateway in range(WORLD):
            if gateway // LOCAL != rank // LOCAL:
                for lane in range(2):
                    slot = (gateway * 4 + 3) * 2 + lane
                    struct.pack_into('<q', completed, slot * ROW_BYTES, MAGIC)
        golden_rank = golden_directory / f'rank{rank:02d}'
        golden_rank.mkdir()
        for name, value in (('returned', expected[rank]), ('completed', completed), ('output', bytes(512))):
            (golden_rank / f'golden-{name}.bin').write_bytes(value)
        memory = [dict(name=name, addr=locations[name], data=value) for name, value in images.items()]
        guards = []
        for name in ('workspace', 'output'):
            for suffix, offset in (('before', -ROW_BYTES), ('after', len(images[name]))):
                guard = f'guard_{name}_{suffix}'
                address = locations[name] + offset
                guards.append(dict(name=guard, addr=address, size=ROW_BYTES, expected_byte=222))
                memory.append(dict(name=guard, addr=address, data=b'\xde' * ROW_BYTES))
        exports = dict(returned=dict(addr=locations['workspace']+config['returnOffset'], size=len(expected[rank])),
                       completed=dict(addr=locations['workspace']+config['completionOffset'], size=len(completed)),
                       output=dict(addr=locations['output'], size=512),
                       status=dict(addr=locations['status'], size=16*32))
        ranks.append(dict(rank=rank, abi_size=160, fields=fields, gm_size=runtime.GM_BYTES,
                          sys_va_base_addr=base, ub_base_addr=0x80000, stack_base_addr=0x100000,
                          param_base_addr=base+0x80000000, binary_addr=base+0x40000000,
                          images=memory, exports=exports, guards=guards))
    identity = dict(tokens=tokens, hidden=128, row_bytes=ROW_BYTES, chunks=4, effective_chunks=1 if mode=='peer' else 4,
                    world=WORLD, topk=TOPK, experts=EXPERTS, seed=seed, mode=mode, route_sha256=route_sha,
                    payload_wqes_by_lane=lanes, so_wqes=WORLD*(WORLD-LOCAL)*2, entry=ENTRY, phase=3,
                    kernel_sha256=digest, runtime_root=runtime_root, source_commit=REVISION)
    write_json(golden_directory / 'manifest.json', identity)
    write_json(golden_directory / 'topk.json', routes)
    loader.prepare_loader(generated, runtime_root, kernel, digest, ENTRY, ranks)
    return identity


def run_case(request, tokens, seed, mode, data, abis, modules):
    case = MOUNT / f'{STEM}t{tokens}-{seed}-{mode}'
    if case.exists():
        report = summarize(case)
        status = report.get('status', 'setup_incomplete')
        if status == 'running':
            raise RuntimeError('existing case is running')
    else:
        with tempfile.TemporaryDirectory(prefix=STEM, dir=MOUNT.parent) as directory:
            temp = Path(directory)
            kernel, generated, golden = temp/'kernel.o', temp/'loader', temp/'golden'
            kernel.write_bytes(gzip.decompress(base64.b64decode(request['object'], validate=True)))
            digest = sha(kernel)
            assert digest == request['object_sha256']
            identity = write_fixture(generated, golden, case, mode, tokens, seed, data, abis, kernel, digest, modules)
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode='w:gz', compresslevel=1) as archive:
                for path in sorted(golden.rglob('*')):
                    if path.is_file():
                        archive.add(path, arcname='fixture-data/' + str(path.relative_to(golden)), recursive=False)
                for path in sorted((generated/'input_bin').rglob('*.bin')):
                    archive.add(path, arcname=str(path.relative_to(generated)), recursive=False)
                for name in ('workload.yml', 'export-layout.json'):
                    archive.add(generated/name, arcname=name, recursive=False)
            fixture = buffer.getvalue()
            prepare_bulk(dict(tokens=tokens, hidden=128, archive=base64.b64encode(fixture).decode(),
                identity=dict(identity, input_archive_sha256=hashlib.sha256(fixture).hexdigest()),
                object=request['object'], checker=request['checker'], exports=request['exports'],
                build=dict(entry=ENTRY, variant=case.name, candidate_commit=REVISION, kernel_sha256=digest,
                           compiled_in='blue')), case, identity['runtime_root'])
        report = execute_phase(case)
    manifest = case/'fixture-data/manifest.json'
    identity = json.loads(manifest.read_text()) if manifest.is_file() else {}
    expected_cqe = sum(data[4]) + WORLD*(WORLD-LOCAL)*2
    result = {name: report[name] for name in ('status', 'sim_us', 'cqe', 'first_mismatch', 'runner_exit_code')
              if name in report}
    result.update(seed=seed, mode=mode, case=case.name, route_sha256=data[3],
                  payload_by_lane=data[4], expected_cqe=expected_cqe)
    if report.get('status') == 'sst_and_checker_passed':
        assert identity['route_sha256'] == data[3]
        assert report['cqe'] == expected_cqe and report['guards']['count'] == 256
        assert report['checked']['ranks'] == WORLD
        result['guard_count'] = report['guards']['count']
        result['checked'] = report['checked']
        result['exports_sha256'] = report['exports_sha256']
    write_json(case/'benchmark-result.json', result)
    return result


def execute_benchmark(request):
    tokens, start, stop = (request[key] for key in ('tokens', 'start', 'stop'))
    assert tokens in (32, 64, 128) and 0 <= start < stop <= 10
    support = MOUNT.parent / BUILD / 'source/tools/unitest/simulator/experiments/inter_server_send/sst'
    sys.path.insert(0, str(support))
    import loader
    import native
    import runtime

    abis = json.loads(gzip.decompress(base64.b64decode(request['abis'], validate=True)))[str(tokens)]
    results = []
    for ordinal in range(start, stop):
        seed = BASE + ordinal
        data = generate_inputs(tokens, seed, abis, (native, runtime))
        modes = MODES if ordinal % 2 == 0 else MODES[::-1]
        pair = [run_case(request, tokens, seed, mode, data, abis, (loader, native, runtime))
                for mode in modes]
        passed = all(row.get('status') == 'sst_and_checker_passed' for row in pair)
        if passed:
            for field in ('checked', 'exports_sha256', 'cqe'):
                assert pair[0][field] == pair[1][field], (seed, field)
        if request.get('compact'):
            for row in pair:
                row.pop('exports_sha256', None)
                row.pop('checked', None)
                row['pair_exports_equal'] = passed
        results.extend(pair)
    return dict(tokens=tokens, object_sha256=request['object_sha256'], results=results)
