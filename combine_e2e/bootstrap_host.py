# Copyright (c) 2026 TileXR Project.
"""Create the task-owned CPU container and compile immutable candidate source."""
import base64
import hashlib
import io
import json
import platform
import subprocess
import tarfile
import time
from pathlib import Path, PurePosixPath

SESSION = 'ea219fe0-2177-4850-84f0-c0da68a4a5fa'
IMAGE = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
ROOT = Path('/mnt/share/tilexr-sim-jobs') / SESSION / 'run-013cc3e-a1'
REVISION = '013cc3e2f8abf8157abdc609afcfe2369806b1e1'


def execute(argv, timeout=20):
    p = subprocess.run(argv, capture_output=True, timeout=timeout)
    assert p.returncode == 0, 'command_failed_' + argv[0]
    return p.stdout


def main(request):
    assert execute(['hostname', '-s']).decode().strip() == 'host-94-26'
    assert platform.machine() == 'aarch64'
    assert request['revision'] == REVISION
    assert execute(['docker','image','inspect','--format','{{.Id}} {{.Os}}/{{.Architecture}}',IMAGE]).decode().strip() == IMAGE+' linux/arm64'
    ROOT.mkdir(parents=True, exist_ok=False)
    record = {k: request[k] for k in ('revision','tree','source_sha256')}
    record.update(root=str(ROOT), image=IMAGE, started_unix=time.time())
    (ROOT/'identity.json').write_text(json.dumps(record,indent=2))
    raw = base64.b64decode(request['source'],validate=True)
    assert hashlib.sha256(raw).hexdigest() == request['source_sha256']
    source = ROOT/'source'
    source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        for member in archive:
            rel=PurePosixPath(member.name)
            assert not rel.is_absolute() and '..' not in rel.parts and (member.isdir() or member.isfile())
            target=source/str(rel)
            if member.isdir(): target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with target.open('xb') as dest: dest.write(archive.extractfile(member).read())
    (ROOT/'build.sh').write_text(request['build_script'])
    name='combine-ht-'+SESSION[:8]+'-013cc3e-a1'
    container=execute(['docker','run','-dt','--name',name,'--runtime=runc','--network=none',
                       '--cap-drop=ALL','--security-opt=no-new-privileges','--cpus=8','--memory=32g',
                       '--pids-limit=1024','--shm-size=64m','--ulimit','nofile=524288:524288',
                       '--label','crosszone.session='+SESSION,'--mount','type=bind,src='+str(ROOT)+',dst=/work',
                       '--mount','type=bind,src='+str(source)+',dst=/work/source,readonly',IMAGE,'/bin/bash','-l']).decode().strip()
    info=json.loads(execute(['docker','inspect',container]))[0]
    assert info['Image']==IMAGE and info['HostConfig']['NetworkMode']=='none'
    record.update(container_id=container, container_name=name)
    hashes=execute(['docker','exec',container,'sha256sum','/opt/sim/cann-env.sh','/opt/sim/plugins/libUBusSimulator.so']).decode().splitlines()
    record['environment_hashes']=dict(line.split(None,1)[::-1] for line in hashes)
    (ROOT/'identity.json').write_text(json.dumps(record,indent=2))
    start=time.monotonic()
    with (ROOT/'container-build.log').open('wb') as log:
        proc=subprocess.run(['docker','exec',container,'timeout','--kill-after=15','1250','/bin/bash','/work/build.sh'],
                            stdout=log,stderr=subprocess.STDOUT,timeout=1290)
    record.update(build_exit=proc.returncode, wall_seconds=time.monotonic()-start)
    summary=ROOT/'build-summary.json'
    record['build']=json.loads(summary.read_text()) if summary.is_file() else {'all_compiled':False,'missing_summary':True}
    (ROOT/'bootstrap-result.json').write_text(json.dumps(record,indent=2))
    print(json.dumps(record,separators=(',',':')))
    return 0 if proc.returncode==0 and record['build']['all_compiled'] else 1
