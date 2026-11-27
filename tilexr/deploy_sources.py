"""Package a pinned TileXR worktree and deploy portable Git checkouts through tssh.

The green bridge owns local archives and staging directories. Failed artifacts
remain available for diagnosis; only successfully consumed remote uploads are
removed. No device workloads, service changes, or system installation are run.
"""

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
from urllib.parse import urlsplit


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def portable_origin(value):
    """Preserve credential-free HTTPS and normal Git SSH upstream addresses."""
    if any(ord(character) < 32 for character in value):
        raise ValueError('INVALID_ORIGIN_CONTROL_CHARACTER')
    if re.fullmatch(r'git@[A-Za-z0-9.-]+:[^\s]+', value):
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in ('https', 'http', 'ssh', 'git') or not parsed.hostname or parsed.password:
        raise ValueError('UNSUPPORTED_ORIGIN_FORMAT')
    if parsed.username and not (parsed.scheme == 'ssh' and parsed.username == 'git'):
        raise ValueError('ORIGIN_CONTAINS_CREDENTIALS')
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', required=True)
    arguments = json.loads(Path(parser.parse_args().args_file).read_text(encoding='utf-8'))
    source = Path(arguments['source']).resolve()
    state_root = Path.cwd()
    artifacts = state_root / 'tilexr-deploy-893851e4'
    artifacts.mkdir(exist_ok=True)
    state_file = artifacts / (arguments['phase'] + '-state.json')
    state = {'phase': arguments['phase'], 'status': 'RUNNING', 'source': str(source)}
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    log_number = 0

    def save():
        temporary = state_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=True), encoding='utf-8')
        temporary.replace(state_file)

    def run(argv, timeout=180):
        nonlocal log_number
        log_number += 1
        stem = artifacts / (arguments['phase'] + f'-{log_number:03d}')
        with stem.with_suffix('.stdout').open('wb') as out, stem.with_suffix('.stderr').open('wb') as err:
            result = subprocess.run(argv, stdout=out, stderr=err, timeout=timeout, env=environment, stdin=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError(f'COMMAND_FAILED:{Path(str(argv[0])).name}:exit={result.returncode}:log={log_number:03d}')
        return stem.with_suffix('.stdout').read_bytes()

    def git(path, *argv, timeout=180):
        return run(['git', '-C', str(path), *argv], timeout).decode('utf-8').strip()

    def repositories(path, relative='.'):
        records = [{'path': relative, 'revision': git(path, 'rev-parse', 'HEAD'), 'origin': portable_origin(git(path, 'config', '--get', 'remote.origin.url'))}]
        tree = run(['git', '-C', str(path), 'ls-tree', '-rz', '--full-tree', 'HEAD'])
        for item in tree.split(b'\0'):
            if not item:
                continue
            metadata, name = item.split(b'\t', 1)
            mode, kind, revision = metadata.decode('ascii').split()
            if mode != '160000':
                continue
            subpath = name.decode('utf-8')
            child = path / subpath
            if git(child, 'rev-parse', 'HEAD') != revision:
                raise RuntimeError('SUBMODULE_REVISION_MISMATCH:' + subpath)
            child_relative = subpath if relative == '.' else relative + '/' + subpath
            records.extend(repositories(child, child_relative))
        return records

    try:
        save()
        assert git(source, 'branch', '--show-current') == arguments['branch'], 'SOURCE_BRANCH_MISMATCH'
        assert git(source, 'rev-parse', 'HEAD') == arguments['revision'], 'SOURCE_REVISION_MISMATCH'
        assert not git(source, 'status', '--porcelain'), 'SOURCE_NOT_CLEAN'
        if arguments['phase'] == 'package':
            state['stage'] = 'initialize-submodules'
            save()
            current = run(['git', '-C', str(source), 'submodule', 'status', '--recursive']).decode('utf-8')
            if any(not line.startswith(' ') for line in current.splitlines()):
                git(source, 'submodule', 'update', '--init', '--recursive', '--depth=1', timeout=1200)
            records = repositories(source)
            state['repositories'] = [{k: record[k] for k in ('path', 'revision')} for record in records]
            save()
            staging_root = Path(arguments.get('staging_root', str(artifacts))).resolve()
            staging_root.mkdir(parents=True, exist_ok=True)
            owner_file = staging_root / '.tilexr-staging-owner.json'
            owner = {'session': '893851e4-b659-488f-8e58-393c9f5a31f2'}
            if owner_file.exists():
                assert json.loads(owner_file.read_text(encoding='utf-8')) == owner, 'STAGING_OWNER_MISMATCH'
            else:
                owner_file.write_text(json.dumps(owner), encoding='utf-8')
            portable = staging_root / ('p' + str(int(time.time())))
            state['staging_root'] = str(staging_root)
            state['portable'] = str(portable)
            state['stage'] = 'portable-clones'
            save()
            for record in records:
                relative = record['path']
                original = source if relative == '.' else source / relative
                target = portable if relative == '.' else portable / relative
                if target.exists():
                    if not target.is_dir() or any(target.iterdir()):
                        raise RuntimeError('PORTABLE_PATH_NOT_EMPTY:' + relative)
                    target.rmdir()
                run(['git', '-c', 'protocol.file.allow=always', '-c', 'core.longpaths=true', 'clone', '--depth=1', '--no-local', '-c', 'core.autocrlf=false', '-c', 'core.symlinks=false', '-c', 'core.longpaths=true', original.as_uri(), str(target)], 300)
                assert git(target, 'rev-parse', 'HEAD') == record['revision']
                origin = portable_origin(record['origin'])
                git(target, 'remote', 'set-url', 'origin', origin)
                if (target / '.gitmodules').is_file():
                    git(target, 'submodule', 'init')
            assert not git(portable, 'status', '--porcelain'), 'PORTABLE_CHECKOUT_NOT_CLEAN'
            submodules = run(['git', '-C', str(portable), 'submodule', 'status', '--recursive']).decode('utf-8')
            assert all(line.startswith(' ') for line in submodules.splitlines()), 'PORTABLE_SUBMODULE_NOT_INITIALIZED'
            manifest = {'revision': arguments['revision'], 'branch': arguments['branch'], 'repositories': records}
            (portable / '.git/tilexr-deployment.json').write_text(json.dumps(manifest, ensure_ascii=True), encoding='utf-8')
            archive = artifacts / 'TileXR-crosszone-893851e4.tar.gz'
            if archive.exists():
                raise RuntimeError('ARCHIVE_ALREADY_EXISTS')
            state['stage'] = 'archive'
            save()
            with tarfile.open(archive, 'w:gz', compresslevel=3, dereference=False) as tar:
                tar.add(portable, arcname='TileXR')
            checksum = file_sha256(archive)
            state.update(status='PACKAGED', archive=str(archive), archive_bytes=archive.stat().st_size,
                         archive_sha256=checksum,
                         submodule_count=len(records) - 1)
            save()
            print(json.dumps({k: state[k] for k in ('status', 'archive', 'archive_bytes', 'archive_sha256', 'repositories', 'submodule_count')}, ensure_ascii=True))
        elif arguments['phase'] == 'finalize':
            package_file = artifacts / 'package-state.json'
            package = json.loads(package_file.read_text(encoding='utf-8'))
            assert package['stage'] == 'archive', 'PACKAGE_ARCHIVE_NOT_REACHED'
            portable = Path(package['portable'])
            manifest = json.loads((portable / '.git/tilexr-deployment.json').read_text(encoding='utf-8'))
            assert manifest['revision'] == arguments['revision']
            assert manifest['branch'] == arguments['branch']
            archive = artifacts / 'TileXR-crosszone-893851e4.tar.gz'
            with gzip.open(archive, 'rb') as stream:
                while stream.read(1024 * 1024):
                    pass
            with tarfile.open(archive, 'r:gz') as tar:
                assert json.load(tar.extractfile('TileXR/.git/tilexr-deployment.json')) == manifest
                heads = [member for member in tar if member.name.endswith('/.git/HEAD') and member.isfile()]
                assert len(heads) == len(manifest['repositories']), 'ARCHIVED_GIT_REPOSITORY_COUNT_MISMATCH'
            package.update(status='PACKAGED', archive=str(archive), archive_bytes=archive.stat().st_size,
                           archive_sha256=file_sha256(archive), submodule_count=len(manifest['repositories']) - 1,
                           archive_validated=True, recovered_from='Python hashlib.file_digest unavailable')
            package.pop('error', None)
            package.pop('error_type', None)
            temporary = package_file.with_suffix('.tmp')
            temporary.write_text(json.dumps(package, ensure_ascii=True), encoding='utf-8')
            temporary.replace(package_file)
            state.update(package)
            save()
            print(json.dumps({k: package[k] for k in ('status', 'archive', 'archive_bytes', 'archive_sha256', 'submodule_count', 'archive_validated')}, ensure_ascii=True))
        elif arguments['phase'] == 'deploy':
            package = json.loads(Path(arguments['package_state']).read_text(encoding='utf-8'))
            assert package['status'] == 'PACKAGED', 'PACKAGE_NOT_READY'
            archive = Path(package['archive'])
            assert file_sha256(archive) == package['archive_sha256']
            transport = archive.with_name('TileXR-crosszone-893851e4.tilexrdata')
            if not transport.exists():
                try:
                    os.link(archive, transport)
                except OSError:
                    shutil.copy2(archive, transport)
            assert file_sha256(transport) == package['archive_sha256'], 'TRANSPORT_CHECKSUM_MISMATCH'
            home = Path.home()
            candidates = [home / '.ssh/npass_id_rsa', home / 'npass_id_rsa', state_root / 'npass_id_rsa'] + [p / 'npass_id_rsa' for p in list(state_root.parents)[:2]]
            key = next((path for path in candidates if path.is_file()), None)
            if key is None:
                raise RuntimeError('KEY_NOT_FOUND')
            ssh = shutil.which('ssh')
            assert ssh, 'SSH_NOT_FOUND'
            module_path = source / 'tools/tssh/tssh.py'
            spec = importlib.util.spec_from_file_location('tilexr_tssh', module_path)
            tssh = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = tssh
            spec.loader.exec_module(tssh)
            inventory = Path(__file__).with_name('inventory.json')
            cluster = tssh.find_cluster(tssh.load_clusters(inventory), 'crosszone-893851e4')
            def ssh_with_key(argv, **kwargs):
                return subprocess.run([argv[0], '-i', str(key), '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none', *argv[1:]], **kwargs)
            def upload(local_path, remote_path, timeout, label):
                command = tssh.remote_command(['timeout', '--signal=TERM', '--kill-after=5', str(max(15, int(timeout) - 10)), 'dd', 'of=' + remote_path, 'conv=excl', 'bs=1M', 'status=none'])
                def stream_file(argv, **kwargs):
                    kwargs.pop('input', None)
                    with local_path.open('rb') as stream:
                        return ssh_with_key(argv, stdin=stream, **kwargs)
                outcomes = tssh.run_cluster(ssh, cluster, command, b'', 2, 10, timeout + 10, run_process=stream_file)
                for outcome in outcomes:
                    prefix = artifacts / (label + '-' + outcome.host.name)
                    prefix.with_suffix('.stdout').write_bytes(outcome.stdout)
                    prefix.with_suffix('.stderr').write_bytes(outcome.stderr)
                state[label + '_results'] = [{'host': outcome.host.address, 'exit_code': outcome.returncode, 'timed_out': outcome.timed_out, 'success': outcome.succeeded} for outcome in outcomes]
                save()
                if not all(outcome.succeeded for outcome in outcomes):
                    raise RuntimeError('SSH_STREAM_TRANSFER_FAILED:' + label)
            remote_archive = '/home/c00996840/ai/.TileXR-crosszone-893851e4-' + package['archive_sha256'][:12] + '.tilexrdata'
            if not arguments.get('resume_remote', False):
                state['stage'] = 'upload-probe'
                state['transfer_method'] = 'ssh-stdin'
                save()
                probe = artifacts / 'TileXR-crosszone-893851e4-probe.tilexrdata'
                probe_payload = b'TileXR source deployment probe 893851e4\n'
                probe.write_bytes(probe_payload)
                remote_probe = '/home/c00996840/ai/TileXR-crosszone-893851e4-probe-ssh.tilexrdata'
                upload(probe, remote_probe, 45, 'probe-upload')
                probe_script = ('import hashlib,json\nfrom pathlib import Path\np=Path(' + repr(remote_probe) + ')\nassert hashlib.sha256(p.read_bytes()).hexdigest()==' + repr(hashlib.sha256(probe_payload).hexdigest()) + '\np.unlink()\nprint(json.dumps({"probe_verified":True,"probe_removed":True}))\n').encode('utf-8')
                state['stage'] = 'upload-probe-verify'
                save()
                probe_results = tssh.run_cluster(ssh, cluster, 'python3 -', probe_script, 2, 10, 45, run_process=ssh_with_key)
                state['probe_results'] = [{'host': result.host.address, 'exit_code': result.returncode, 'success': result.succeeded} for result in probe_results]
                save()
                assert all(result.succeeded and json.loads(result.stdout.decode('utf-8')).get('probe_verified') for result in probe_results), 'UPLOAD_PROBE_VERIFICATION_FAILED'
                probe.unlink()
                state['stage'] = 'upload'
                state['transport'] = str(transport)
                state['uploads'] = []
                save()
                upload_timeout = min(1800, 300 + archive.stat().st_size // (1024 * 1024))
                upload(transport, remote_archive, upload_timeout, 'archive-upload')
                state['uploads'] = [host.address for host in cluster.hosts]
                save()
            else:
                state['resume_remote'] = True
            remote_code = Path(__file__).with_name('remote_deploy.py').read_bytes()
            command = tssh.remote_command(['python3', '-', remote_archive, arguments['destination'], package['archive_sha256']])
            state['stage'] = 'remote-deploy'
            save()
            results = tssh.run_cluster(ssh, cluster, command, remote_code, 2, 10, 900, run_process=ssh_with_key)
            summaries = []
            for result in results:
                log_stem = artifacts / ('remote-' + result.host.name)
                log_stem.with_suffix('.stdout').write_bytes(result.stdout)
                log_stem.with_suffix('.stderr').write_bytes(result.stderr)
                if not result.succeeded:
                    summaries.append({'host': result.host.address, 'status': 'FAILED', 'exit_code': result.returncode, 'timed_out': result.timed_out})
                else:
                    summaries.append({'host': result.host.address, **json.loads(result.stdout.decode('utf-8'))})
            state['results'] = summaries
            if not all(result.succeeded for result in results):
                raise RuntimeError('REMOTE_DEPLOYMENT_FAILED')
            state['status'] = 'DEPLOYED'
            transport.unlink()
            save()
            print(json.dumps({'status': 'DEPLOYED', 'results': summaries}, ensure_ascii=True))
        else:
            raise RuntimeError('UNKNOWN_PHASE')
    except BaseException as exc:
        state['status'] = 'FAILED'
        state['error_type'] = type(exc).__name__
        state['error'] = str(exc)[:1000]
        save()
        print(json.dumps({'status': 'FAILED', 'stage': state.get('stage'), 'error_type': state['error_type'], 'state_file': str(state_file)}, ensure_ascii=True))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
