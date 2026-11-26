"""Install and execute one standalone sender case on the retained SST model."""
import base64
import hashlib
import io
import json
import sys
import tarfile
import tempfile
from pathlib import Path
from time import monotonic
import zipfile


def send_case(metadata):
    return 'ht-tunable-send-186dfa18-bs32-{}-{}-{}'.format(metadata['six'], metadata['two'], metadata['policy'])


def run_send(request):
    six, two, policy = (request[name] for name in ('six', 'two', 'policy'))
    case = MOUNT / send_case(request)
    if request.get('status'):
        record = case / 'run.json'
        log = case / 'sim.log'
        return dict(exists=case.exists(), run=json.loads(record.read_text()) if record.is_file() else {},
                    cqe=log.read_bytes().count(b'WrCQEResp') if log.is_file() else None)
    runtime = type(sys)('candidate_runtime')
    exec(compile(request['runtime'], '<candidate-runtime>', 'exec'), runtime.__dict__)
    loader = type(sys)('candidate_loader')
    exec(compile(request['loader'], '<candidate-loader>', 'exec'), loader.__dict__)
    if request.get('cq'):
        info = json.loads((case / 'fixture-data/manifest.json').read_text())
        return count_send_cq(case, info, runtime)

    assert not case.exists()
    loader_root = '/work/inter-server-native/' + case.name
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(request['plans'], validate=True))) as archive:
        abis = [json.loads(archive.read(f'{rank}/abi.json')) for rank in range(64)]
        plans = [archive.read(f'{rank}/{six}-{two}-{POLICIES[policy]}.bin') for rank in range(64)]
    bindings, golden, routes, info = build_send_fixture(abis, plans, six, two, policy, runtime, loader)
    identity = dict(info, tokens=TOKENS, hidden=128, chunks=4, entry=SEND_ENTRY, runtime_root=loader_root,
                    phase=3, blocks=SEND_CORES, seed=SEED, six=six, two=two, policy=policy,
                    source_commit=request['revision'], kernel_sha256=request['sha256'], profiling=False)
    with tempfile.TemporaryDirectory(prefix=case.name + '-', dir=MOUNT.parent) as directory:
        temp = Path(directory)
        kernel = temp / 'kernel.o'
        kernel.write_bytes(gzip.decompress(base64.b64decode(request['object'], validate=True)))
        assert sha(kernel) == request['sha256']
        generated = temp / 'loader'
        loader.prepare_loader(generated, loader_root, kernel, request['sha256'], SEND_ENTRY, bindings, blocks=SEND_CORES)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz', compresslevel=1) as archive:
            for rank, images in enumerate(golden):
                for name, data in images.items():
                    add_bytes(archive, f'fixture-data/rank{rank:02d}/golden-{name}.bin', data)
            add_bytes(archive, 'fixture-data/manifest.json', json.dumps(identity).encode())
            add_bytes(archive, 'fixture-data/topk.json', json.dumps(routes).encode())
            for path in (generated / 'input_bin').rglob('*.bin'):
                archive.add(path, arcname=str(path.relative_to(generated)), recursive=False)
            for name in ('workload.yml', 'export-layout.json'):
                archive.add(generated / name, arcname=name, recursive=False)
        blob = buffer.getvalue()
        prepare_bulk(dict(tokens=TOKENS, hidden=128, archive=base64.b64encode(blob).decode(),
                          identity=dict(identity, input_archive_sha256=hashlib.sha256(blob).hexdigest()),
                          object=request['object'], checker=request['checker'], exports=request['exports'],
                          build=dict(entry=SEND_ENTRY, variant=case.name, candidate_commit=request['revision'],
                                     kernel_sha256=request['sha256'], compiled_in='blue')), case, loader_root)
    started = monotonic()
    report = execute_phase(case)
    record = json.loads((case / 'run.json').read_text())
    report.update({key: value for key, value in identity.items() if key != 'expected_cq'})
    report['runner_wall_seconds'] = round(monotonic() - started, 3)
    if 'sst_wall_seconds' in record:
        report['sst_wall_seconds'] = round(record['sst_wall_seconds'], 3)
    write_json(case / 'send-result.json', report)
    return report


def add_bytes(archive, name, data):
    member = tarfile.TarInfo(name)
    member.size = len(data)
    archive.addfile(member, io.BytesIO(data))


def count_send_cq(case, info, runtime):
    expected = [{} for _ in range(64)]
    for rank, peer, physical, count in info['expected_cq']:
        expected[rank][peer * 40 + physical] = [('cross_return', range(count))]
    result = count_candidate_cq(case, info['tokens'], expected)
    if result['first_missing']:
        result['arrival'] = count_send_arrivals(case, info, runtime, result['first_missing'])
    return result


def count_send_arrivals(case, info, runtime, missing):
    """Match each first-missing slot to its intended remote payload or SO address."""
    routes = json.loads((case / 'fixture-data/topk.json').read_bytes())
    result = []
    for sender, peer, physical, _, _, count, _, absent, _, _ in missing:
        # The case keeps the exact installed ABI and receive trace.
        parameter = (case / 'input_bin' / f'rank{sender:02d}' / 'kernel_args.bin').read_bytes()
        completion_offset, return_offset = struct.unpack_from('<2Q', parameter, 48 + 64)
        lane = physical // 20
        tokens = []
        for token, experts in enumerate(routes[peer]):
            same_server = [expert // 4 for expert in experts if expert // 32 == sender // 8]
            if same_server and same_server[0] == sender:
                tokens.append(token)
        tokens = [token for ordinal, token in enumerate(tokens) if (ordinal % 4 == 3) == bool(lane)]
        assert len(tokens) + 1 == count
        base = peer * runtime.GM_BYTES + runtime.BASES['workspace']
        addresses = [base + return_offset + (token * 8 + sender // 8) * 512 for token in tokens]
        addresses.append(base + completion_offset + ((sender * 4 + 3) * 2 + lane) * 512)
        targets = {addresses[slot]: ('so' if slot == len(tokens) else tokens[slot]) for slot in absent}
        arrival = set()
        path = case / 'OUTPUT/stat_trace' / f'Host{peer}_ba_layer_trace.json'
        if path.is_file():
            with path.open('rb') as stream:
                raw = stream.read(MAX_TRACE_BYTES)
            for match in TRACE_ARGS.finditer(raw):
                if b'"WR_DATA"' not in match[0]:
                    continue
                args = json.loads(match[0])
                value = args['oriAddr_']
                address = int(value, 0) if isinstance(value, str) else int(value)
                if address in targets:
                    arrival.add(address)
        result.append([sender, peer, physical, [[targets[addr], addr in arrival] for addr in targets]])
    return result
