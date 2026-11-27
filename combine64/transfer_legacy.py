"""Copy the verified reference image and platform between green hosts."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import time
import traceback

from check_sim_target import connect, load_target
from preflight_target import HOST, read_target_config
from ssh_target import SESSION

IMAGE = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
ARCHIVE = '/mnt/share/simulator/images/tilexr-sim-9.1-aarch64-v1.0.0.tar.gz'
MAX_BYTES = 12 * 2**30

REFERENCE = r"""
import hashlib, io, json, pathlib, sys, tarfile
p = pathlib.Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549')
assert json.loads((p/'run.json').read_text())['status'] == 'sst_and_checker_passed'
manifest = json.loads((p/'case-manifest.json').read_text())['files']
files = {name: expected for name, expected in manifest.items()
         if name in ('test_UBusSim.py','environment.snapshot.json','sst-environment.json','installed_model.py')
         or (name.startswith(('v310_config/','UBusSim_config/')) and not name.endswith('workload_config.yml'))}
assert all(name in files for name in ('test_UBusSim.py','environment.snapshot.json','sst-environment.json','installed_model.py'))
assert len(files) <= 40
with tarfile.open(mode='w|gz',fileobj=sys.stdout.buffer) as out:
    for name, expected in sorted(files.items()):
        rel=pathlib.PurePosixPath(name)
        assert not rel.is_absolute() and '..' not in rel.parts
        src=p/name
        assert src.is_file() and src.stat().st_size <= 4*1024*1024, name
        content=src.read_bytes()
        assert hashlib.sha256(content).hexdigest() == expected, name
        asset=tarfile.TarInfo(name)
        asset.size=len(content)
        out.addfile(asset,io.BytesIO(content))
    data=json.dumps(files).encode()
    item=tarfile.TarInfo('.manifest.json')
    item.size=len(data)
    out.addfile(item,io.BytesIO(data))
"""


def main():
    import paramiko

    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', required=True, type=Path)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    data, _ = read_target_config(args['fingerprint'])
    target = load_target(data, HOST)
    key = Path(os.environ['USERPROFILE']) / '.ssh' / 'id_ed25519'
    source = ['ssh','-T','-i',str(key),'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
              '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','root@141.61.94.26']
    if args.get('action') == 'inspect_reference':
        probe = REFERENCE.split('with tarfile.open(', 1)[0] + """
import os
print(json.dumps({name: {'exists': (p/name).is_file(),
                       'size': (p/name).stat().st_size if (p/name).is_file() else None,
                       'link': os.readlink(p/name) if (p/name).is_symlink() else None}
                  for name in sorted(files)}))
"""
        result = subprocess.run(source + ['timeout --kill-after=5 30 python3 -'],input=probe.encode(),
                                capture_output=True,timeout=45)
        values = json.loads(result.stdout) if result.returncode == 0 else {}
        print(json.dumps({'ssh_exit':result.returncode,'reference_files':values}))
        return 0

    started = time.monotonic()

    def destination(client, deployment):
        root = str(PurePosixPath(deployment['workspace'])/'tilexr-sim-jobs'/SESSION/'legacy-assets')
        assert re.fullmatch(r'/[A-Za-z0-9_./-]+', root)

        def remote(cmd, secs=30):
            _, stdout, stderr = client.exec_command(cmd, timeout=secs)
            out = stdout.read(20000)
            err = stderr.read(4000)
            rc = stdout.channel.recv_exit_status()
            if rc:
                raise RuntimeError('destination_command_' + str(rc))
            return out

        remote(f'mkdir -p -- {root}')
        with client.open_sftp() as sftp:
            mode = args.get('action', 'transfer')
            if mode == 'status':
                print(json.dumps({'files': {item.filename: item.st_size for item in sftp.listdir_attr(root)}}))
                return 0
            archive = root + '/tilexr-sim-v1.0.0.tar.gz'
            partial = archive + '.partial'

            def hash_remote(path):
                return remote(f'timeout 240 sha256sum {path}', 255).decode().split()[0]

            reference = subprocess.run(source + ['timeout --kill-after=5 45 python3 -'], input=REFERENCE.encode(),
                                       capture_output=True, timeout=60)
            if reference.returncode or not 0 < len(reference.stdout) < 1024*1024:
                error = reference.stderr.decode(errors='replace')
                print(json.dumps({'status':'reference_platform_failed','ssh_exit':reference.returncode,
                                  'platform_archive_bytes':len(reference.stdout),
                                  'remote_error':re.findall(r'(?m)^([A-Za-z]+Error):?',error)[-3:],
                                  'remote_frames':re.findall(r'File "<stdin>", line (\d+)',error)[-3:]}))
                return 0
            with tarfile.open(fileobj=io.BytesIO(reference.stdout),mode='r:gz') as package:
                members = package.getmembers()
                assert all(m.isfile() and not PurePosixPath(m.name).is_absolute()
                           and '..' not in PurePosixPath(m.name).parts for m in members)
                manifest = json.load(package.extractfile('.manifest.json'))
                assert all(hashlib.sha256(package.extractfile(name).read()).hexdigest() == expected
                           for name, expected in manifest.items())
                assert set(manifest) == {m.name for m in members} - {'.manifest.json'}
            with sftp.open(root+'/platform.tar.gz','wb') as file:
                file.write(reference.stdout)

            if mode == 'transfer':
                try:
                    sftp.stat(archive)
                except FileNotFoundError:
                    sha = subprocess.run(source+['timeout --kill-after=5 240 sha256sum '+ARCHIVE],
                                         capture_output=True,timeout=255)
                    if sha.returncode:
                        print(json.dumps({'status':'archive_hash_failed','ssh_exit':sha.returncode}))
                        return 0
                    expected = sha.stdout.decode().split()[0]
                    assert re.fullmatch(r'[a-f0-9]{64}', expected)
                    count, value = 0, hashlib.sha256()
                    with subprocess.Popen(source+['timeout --kill-after=10 1900 cat '+ARCHIVE],
                                          stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) as sender:
                        try:
                            with sftp.open(partial,'wx',bufsize=1024*1024) as file:
                                file.set_pipelined(True)
                                while chunk := sender.stdout.read(1024*1024):
                                    count += len(chunk)
                                    assert count <= MAX_BYTES
                                    value.update(chunk)
                                    file.write(chunk)
                            assert sender.wait(timeout=20) == 0
                        finally:
                            if sender.poll() is None:
                                sender.kill()
                    assert value.hexdigest() == expected == hash_remote(partial)
                    sftp.rename(partial, archive)
                    remote(f'printf %s {expected} > {archive}.sha256')
            expected = remote(f'cat {archive}.sha256').decode()
            assert re.fullmatch(r'[a-f0-9]{64}', expected) and hash_remote(archive) == expected
            installed = remote(f'docker image inspect --format "{{{{.Id}}}} {{{{.Architecture}}}}" {IMAGE} 2>/dev/null || true').decode().strip()
            if not installed:
                remote(f'timeout --kill-after=20 650 docker load -i {archive}', 680)
                installed = remote(f'docker image inspect --format "{{{{.Id}}}} {{{{.Architecture}}}}" {IMAGE}').decode().strip()
            assert installed == IMAGE + ' arm64'
            print(json.dumps({'status':'legacy_assets_ready','root':root,'archive_size':sftp.stat(archive).st_size,
                              'archive_sha256':expected,'image_id':IMAGE,'platform_files':len(manifest),
                              'wall_seconds':round(time.monotonic()-started,3)}))
            return 0

    try:
        return connect(paramiko, target, destination)
    except Exception as error:
        frames = [{'function':f.name,'line':f.lineno}
                  for f in traceback.extract_tb(error.__traceback__) if Path(f.filename).name == Path(__file__).name]
        print(json.dumps({'status':'legacy_transfer_failed','error_type':type(error).__name__,'frames':frames}))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
