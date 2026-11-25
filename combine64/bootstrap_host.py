# Copyright (c) 2026 TileXR Project.
"""Create the task-owned CPU container and compile immutable candidate source."""
import base64
import fcntl
import hashlib
import io
import json
import platform
import subprocess
import tarfile
import time
from pathlib import Path, PurePosixPath

SESSION = 'de038086-2b21-40e2-bad9-65360186ad97'
IMAGE = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
ROOT = Path('/mnt/share/tilexr-sim-jobs') / SESSION / 'run-86f802f-a1'
REVISION = '86f802f9f8a19728364c466b0e9cb7416e038595'


def execute(argv, timeout=20):
    p = subprocess.run(argv, capture_output=True, timeout=timeout)
    assert p.returncode == 0, 'command_failed_' + argv[0]
    return p.stdout


def build(request):
    assert execute(['hostname', '-s']).decode().strip() == 'host-94-26'
    assert platform.machine() == 'aarch64'
    assert request['revision'] == REVISION
    assert execute(['docker','image','inspect','--format','{{.Id}} {{.Os}}/{{.Architecture}}',IMAGE]).decode().strip() == IMAGE+' linux/arm64'
    ROOT.mkdir(parents=True, exist_ok=False)
    record = {k: request[k] for k in ('revision','tree','source_sha256')}
    record.update(root=str(ROOT), image=IMAGE, started_unix=time.time(), sim_send=True, model='perf', world=64, hidden=128, topk=6, dtype='fp32')
    (ROOT/'identity.json').write_text(json.dumps(record,indent=2))
    raw = base64.b64decode(request['source'],validate=True)
    assert hashlib.sha256(raw).hexdigest() == request['source_sha256']
    template = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549')
    assert json.loads((template/'run.json').read_text())['status'] == 'sst_and_checker_passed'
    environment = json.loads((template/'sst-environment.json').read_text())
    record['platform_template'] = str(template)
    record['platform_environment_sha256'] = hashlib.sha256((template/'sst-environment.json').read_bytes()).hexdigest()
    record['platform_runtime_file_count'] = len(environment.get('runtime_files', {}))
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
    name='combine-ht-'+SESSION[:8]+'-86f802f-a1'
    container=execute(['docker','run','-dt','--name',name,'--runtime=runc','--network=none',
                       '--cap-drop=ALL','--security-opt=no-new-privileges','--cpus=8','--memory=32g',
                       '--pids-limit=1024','--shm-size=64m','--ulimit','nofile=524288:524288',
                       '--label','crosszone.session='+SESSION,'--mount','type=bind,src='+str(ROOT)+',dst=/work',
                       '--mount','type=bind,src='+str(source)+',dst=/work/source,readonly',IMAGE,'/bin/bash','-c','exec tail -f /dev/null']).decode().strip()
    info=json.loads(execute(['docker','inspect',container]))[0]
    assert info['Image']==IMAGE and info['HostConfig']['NetworkMode']=='none'
    record.update(container_id=container, container_name=name)
    assert info['State']['Running'] and info['HostConfig']['Runtime']=='runc'
    assert info['HostConfig']['Memory']==32*2**30
    assert info['Config']['Labels']['crosszone.session']==SESSION
    record['container_network'] = info['HostConfig']['NetworkMode']
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


def main(request):
    baseline_lock = ROOT.parent / 'run-b5383cf-a1' / 'sst.lock'
    if baseline_lock.is_file():
        with baseline_lock.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return build(request)
    return build(request)
