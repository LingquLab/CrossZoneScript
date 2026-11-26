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

SESSION = '44b9c060-a5dd-46a8-897f-e4a6fee171e2'
IMAGE = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
SESSION_ROOT = Path('/mnt/share/tilexr-sim-jobs') / SESSION
REVISIONS = {
    '518b9df': '518b9dfa02dc3d33852ebecea90143d569ea50af',
    'd04fde0': 'd04fde0c3d7b3e9017ca8d43383b9183fd7a2354',
    '8e005fa': '8e005faf781c14dab3c0e977b52741ee29b597ac',
    '85d4b8d': '85d4b8de182b0c813d586f7278920489c0c5c4a6',
    'b6b968a': 'b6b968a562ab5a3a762ecedfd3e4c45b545f5b55',
    'e30fad2': 'e30fad2e8f01c90d4d0aa5cfa5d2cf6e4c881ac1',
}


def execute(argv, timeout=20):
    p = subprocess.run(argv, capture_output=True, timeout=timeout)
    assert p.returncode == 0, 'command_failed_' + argv[0]
    return p.stdout


def build(request):
    assert execute(['hostname', '-s']).decode().strip() == 'host-94-26'
    assert platform.machine() == 'aarch64'
    candidate = request['candidate']
    assert request['revision'] == REVISIONS[candidate]
    root = SESSION_ROOT / f'run-{candidate}-a1'
    assert execute(['docker','image','inspect','--format','{{.Id}} {{.Os}}/{{.Architecture}}',IMAGE]).decode().strip() == IMAGE+' linux/arm64'
    root.mkdir(parents=True, exist_ok=False)
    record = {k: request[k] for k in ('revision','tree','source_sha256')}
    record.update(root=str(root), image=IMAGE, started_unix=time.time(), sim_send=True, model='perf', world=64, hidden=128, topk=6, dtype='fp32', experts=256)
    (root/'identity.json').write_text(json.dumps(record,indent=2))
    raw = base64.b64decode(request['source'],validate=True)
    assert hashlib.sha256(raw).hexdigest() == request['source_sha256']
    template = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549')
    assert json.loads((template/'run.json').read_text())['status'] == 'sst_and_checker_passed'
    environment = json.loads((template/'sst-environment.json').read_text())
    record['platform_template'] = str(template)
    record['platform_environment_sha256'] = hashlib.sha256((template/'sst-environment.json').read_bytes()).hexdigest()
    record['platform_runtime_file_count'] = len(environment.get('runtime_files', {}))
    source = root/'source'
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
    (root/'build.sh').write_text(request['build_script'])
    profile_tools = root / 'bootstrap-tools'
    profile_tools.mkdir()
    assert request['profile_files'].keys() == {'build_profile.py', 'profile_capture.h'}
    for name, code in request['profile_files'].items():
        (profile_tools / name).write_text(code)
    limit_gib = 32 if candidate == '518b9df' else 64
    name=f'combine-ht-{SESSION[:8]}-{candidate}-a1'
    container=execute(['docker','run','-dt','--name',name,'--runtime=runc','--network=none',
                       '--cap-drop=ALL','--security-opt=no-new-privileges','--cpus=8',f'--memory={limit_gib}g',f'--memory-swap={limit_gib}g',
                       '--pids-limit=1024','--shm-size=64m','--ulimit','nofile=524288:524288',
                       '--label','crosszone.session='+SESSION,'--mount','type=bind,src='+str(root)+',dst=/work',
                       '--mount','type=bind,src='+str(source)+',dst=/work/source,readonly',IMAGE,'/bin/bash','-c','exec tail -f /dev/null']).decode().strip()
    info=json.loads(execute(['docker','inspect',container]))[0]
    assert info['Image']==IMAGE and info['HostConfig']['NetworkMode']=='none'
    record.update(container_id=container, container_name=name)
    assert info['State']['Running'] and info['HostConfig']['Runtime']=='runc'
    assert info['HostConfig']['Memory']==limit_gib*2**30
    record['memory_bytes'] = info['HostConfig']['Memory']
    assert info['Config']['Labels']['crosszone.session']==SESSION
    record['container_network'] = info['HostConfig']['NetworkMode']
    hashes=execute(['docker','exec',container,'sha256sum','/opt/sim/cann-env.sh','/opt/sim/plugins/libUBusSimulator.so']).decode().splitlines()
    record['environment_hashes']=dict(line.split(None,1)[::-1] for line in hashes)
    (root/'identity.json').write_text(json.dumps(record,indent=2))
    start=time.monotonic()
    with (root/'container-build.log').open('wb') as log:
        proc=subprocess.run(['docker','exec',container,'timeout','--kill-after=15','1250','/bin/bash','/work/build.sh'],
                            stdout=log,stderr=subprocess.STDOUT,timeout=1290)
    record.update(build_exit=proc.returncode, wall_seconds=time.monotonic()-start)
    summary=root/'build-summary.json'
    record['build']=json.loads(summary.read_text()) if summary.is_file() else {'all_compiled':False,'missing_summary':True}
    if proc.returncode == 0 and record['build']['all_compiled']:
        profile_start = time.monotonic()
        command = ('source /opt/sim/cann-env.sh; '
                   'export PATH=/opt/sim/Ascend/cann-9.1.0/tools/bisheng_compiler/bin:'
                   '/opt/sim/Ascend/cann-9.1.0/aarch64-linux/ccec_compiler/bin:$PATH; '
                   'exec python3 /work/bootstrap-tools/build_profile.py')
        with (root/'profile-build.log').open('xb') as log:
            profile = subprocess.run(['docker','exec',container,'timeout','--kill-after=15','420',
                                      'bash','-c',command], stdout=log,stderr=subprocess.STDOUT,timeout=445)
        summary = root/'profile-build/build-summary.json'
        record['profile'] = json.loads(summary.read_text()) if summary.is_file() else {'all_compiled': False}
        record.update(profile_exit=profile.returncode, profile_build_seconds=time.monotonic()-profile_start)
    (root/'bootstrap-result.json').write_text(json.dumps(record,indent=2))
    print(json.dumps(record,separators=(',',':')))
    return 0 if proc.returncode==0 and record['build']['all_compiled'] and record.get('profile_exit') == 0 else 1


def main(request):
    return build(request)
