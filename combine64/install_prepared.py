# Copyright (c) 2026 TileXR Project.
"""Verify and install one blue-prepared fixture, then bind a green compiled object."""
import base64,hashlib,io,json,shutil,struct,tarfile
from pathlib import Path

def install(root,request):
    root=Path(root);tokens=request['tokens'];portable=request['portable'];identity=portable['identity']
    assert identity['tokens']==tokens and identity['world']==64 and identity['candidate_commit']=='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3'
    assert identity['kernel_binding']=='deferred_to_green_compiled_40596e2_object'
    payload=base64.b64decode(portable['archive'],validate=True)
    assert hashlib.sha256(payload).hexdigest()==identity['archive_sha256']
    asset=root/'assets'/('64p-t%d'%tokens);asset.mkdir(parents=True,exist_ok=False)
    unpack=asset/'portable';unpack.mkdir()
    with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as tar:
        for member in tar:
            p=Path(member.name)
            assert member.isfile() and not p.is_absolute() and '..' not in p.parts
            assert p.parts[0] in ('input_bin','fixture-data','routes.json','workload.yml','export-layout.json')
            target=unpack/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(tar.extractfile(member).read())
    loader=asset/'loader';loader.mkdir()
    for name in ('input_bin','workload.yml','export-layout.json'):shutil.move(str(unpack/name),str(loader/name))
    for name in ('fixture-data','routes.json'):shutil.move(str(unpack/name),str(asset/name))
    assert hashlib.sha256((asset/'routes.json').read_bytes()).hexdigest()==identity['routes_sha256']
    build=json.loads((root/'profile-build/result.json').read_text())['targets']['local_ht'];binary=(root/build['path']).read_bytes()
    assert hashlib.sha256(binary).hexdigest()==build['sha256']
    (loader/'kernel').mkdir();padded=binary+bytes(10485760-len(binary));(loader/'kernel/inter_server_send.o').write_bytes(padded)
    identity.update(kernel_sha256=hashlib.sha256(padded).hexdigest(),kernel_binding='green_compiled_object',green_object_sha256=build['sha256'])
    meta=json.loads((asset/'fixture-data/manifest.json').read_text());meta['kernel_sha256']=identity['kernel_sha256'];(asset/'fixture-data/manifest.json').write_text(json.dumps(meta,indent=2))
    for rank in range(64):
        folder=loader/'input_bin'/('rank%02d'%rank)
        assert len((folder/'kernel_args.bin').read_bytes())==248
        assert struct.unpack_from('<5I',(folder/'kernel_args.bin').read_bytes(),112)==(rank,64,tokens,128,6)
        forward=(folder/'forward.bin').read_bytes();assert len(forward)==64*tokens*6*6*4
        assert all((folder/(n+'.bin')).is_file() for n in ('input','workspace','status','profiling'))
    (asset/'identity.json').write_text(json.dumps(identity,indent=2))
    (asset/'send-abis.json').write_text(json.dumps(portable['abis'],separators=(',',':')))
    return asset,identity
