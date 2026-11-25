"""Install a unique pinned unified workload using the verified SST fixture runner."""
import io
import tempfile
import zipfile


def prepare(request, case, support):
    sys.path.insert(0,str(support))
    import native
    import runtime
    loader = type(sys)('parallel_loader')
    exec(compile(request['loader'],'<parallel-loader>','exec'),loader.__dict__)
    plans_raw=base64.b64decode(request['plans'],validate=True)
    assert hashlib.sha256(plans_raw).hexdigest()==request['plans_sha256']
    data=gzip.decompress(base64.b64decode(request['object'],validate=True))
    assert hashlib.sha256(data).hexdigest()==KERNEL_SHA256
    runtime_root='/work/inter-server-native/'+case.name
    identity=dict(tokens=TOKENS,hidden=HIDDEN,chunks=TOKENS,entry=ENTRY,
                  runtime_root=runtime_root,phase=1,blocks=BLOCKS)
    with tempfile.TemporaryDirectory(prefix=case.name+'-',dir=MOUNT.parent) as directory:
        temp=Path(directory)
        kernel=temp/'kernel.o'
        kernel.write_bytes(data)
        golden=temp/'golden'
        golden.mkdir()
        write_json(golden/'manifest.json',identity)
        ranks,trace=[],[]
        with zipfile.ZipFile(io.BytesIO(plans_raw)) as plans:
            for rank in range(64):
                binding,expected,offsets=build_rank(rank,json.loads(plans.read(f'{rank}/abi.json')),
                                                   plans.read(f'{rank}/args'),plans.read(f'{rank}/prefill'),
                                                   (native,runtime))
                ranks.append(binding)
                trace.append(offsets)
                name=golden/f'rank{rank:02d}'
                name.mkdir()
                for field,raw in expected.items():
                    (name/('golden-'+field+'.bin')).write_bytes(raw)
        generated=temp/'loader'
        loader.prepare_loader(generated,runtime_root,kernel,KERNEL_SHA256,ENTRY,ranks,blocks=BLOCKS)
        write_json(golden/'trace-offsets.json',trace)
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w:gz',compresslevel=1) as archive:
            for path in golden.rglob('*'):
                if path.is_file():
                    archive.add(path,arcname='fixture-data/'+str(path.relative_to(golden)),recursive=False)
            for path in (generated/'input_bin').rglob('*.bin'):
                archive.add(path,arcname=str(path.relative_to(generated)),recursive=False)
            for name in ('workload.yml','export-layout.json'):
                archive.add(generated/name,arcname=name,recursive=False)
        fixture=buffer.getvalue()
        prepare_bulk(dict(tokens=TOKENS,hidden=HIDDEN,archive=base64.b64encode(fixture).decode(),
                          identity=dict(identity,input_archive_sha256=hashlib.sha256(fixture).hexdigest()),
                          object=request['object'],checker=request['checker'],exports=request['exports'],
                          build=dict(entry=ENTRY,variant=case.name,candidate_commit=request['revision'],
                                     kernel_sha256=KERNEL_SHA256,compiled_in='blue')),case,runtime_root)


def main(request):
    case=MOUNT/CASE
    assert not case.exists(), 'The case already exists'
    support=MOUNT.parent/BUILD/'source/tools/unitest/simulator/experiments/inter_server_send/sst'
    prepare(request,case,support)
    phase=execute_phase(case)
    report={name:phase[name] for name in ('case','status','returncode','runner_exit_code','kernel_sha256',
                                          'sim_us','cqe','checked','guards','exports_sha256') if name in phase}
    if (case/'check.log').is_file() and 'checked' not in report:
        for line in (case/'check.log').read_text(errors='replace').splitlines():
            if line.startswith('{"ranks":'):
                report['checker_detail']=json.loads(line)
                break
    report['trace']=summarize_trace(case)
    write_json(case/'unified-result.json',report)
    return report
