"""Validate an uploaded portable Git archive and publish it without overwriting.

Only this deployment's upload and staging directory are owned here. On failure,
retain both for diagnosis. Successful publication removes only the upload.
"""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile


def git(path, *args):
    result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError('GIT_VALIDATION_FAILED:' + args[0] + ':exit=' + str(result.returncode))
    return result.stdout.decode('utf-8').strip()


def main():
    archive, destination = Path(sys.argv[1]), Path(sys.argv[2])
    checksum = sys.argv[3]
    base = Path('/home/c00996840/ai').resolve()
    assert destination.parent.resolve() == base
    assert destination.name == 'TileXR-crosszone-893851e4'
    assert archive.parent.resolve() == base
    assert archive.name == '.TileXR-crosszone-893851e4-' + checksum[:12] + '.tar.gz'
    assert not destination.exists(), 'DESTINATION_ALREADY_EXISTS'
    with archive.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == checksum, 'ARCHIVE_CHECKSUM_MISMATCH'
    stage = base / ('.TileXR-crosszone-893851e4.stage-' + checksum[:12])
    stage.mkdir(mode=0o755)
    (stage / 'owner.json').write_text(json.dumps({'session': '893851e4-b659-488f-8e58-393c9f5a31f2', 'archive_sha256': checksum}))
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        for member in members:
            parts = Path(member.name).parts
            if not parts or parts[0] != 'TileXR' or '..' in parts or member.name.startswith('/') or not (member.isfile() or member.isdir()):
                raise RuntimeError('UNSAFE_ARCHIVE_MEMBER')
        tar.extractall(stage, members=members)
    source = stage / 'TileXR'
    manifest = json.loads((source / '.git/tilexr-deployment.json').read_text())
    verified = []
    for record in reversed(manifest['repositories']):
        relative = record['path']
        repository = source if relative == '.' else source / relative
        assert repository.resolve().is_relative_to(source.resolve())
        assert (repository / '.git').is_dir(), 'NONPORTABLE_GIT_DIRECTORY'
        git(repository, 'config', '--local', 'core.autocrlf', 'false')
        git(repository, 'config', '--local', 'core.filemode', 'true')
        git(repository, 'config', '--local', 'core.symlinks', 'true')
        git(repository, 'reset', '--hard', record['revision'])
        assert git(repository, 'rev-parse', 'HEAD') == record['revision']
        git(repository, 'fsck', '--full')
        assert not git(repository, 'status', '--porcelain', '--untracked-files=normal'), 'DIRTY_CHECKOUT'
        verified.append({'path': relative, 'revision': record['revision']})
    assert git(source, 'branch', '--show-current') == manifest['branch']
    assert not git(source, 'status', '--porcelain'), 'DIRTY_MAIN_CHECKOUT'
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        raise OSError(ctypes.get_errno(), 'ATOMIC_PUBLISH_FAILED')
    assert git(destination, 'rev-parse', 'HEAD') == manifest['revision']
    assert not git(destination, 'status', '--porcelain')
    archive.unlink()
    (stage / 'owner.json').unlink()
    stage.rmdir()
    print(json.dumps({'status': 'DEPLOYED', 'destination': str(destination), 'revision': manifest['revision'], 'branch': manifest['branch'], 'repositories': verified, 'archive_sha256': checksum, 'checkout_clean': True, 'git_fsck_passed': True, 'upload_removed': True, 'build_performed': False, 'device_tests_performed': False}, ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        print(json.dumps({'status': 'FAILED', 'error_type': type(exc).__name__, 'reason': str(exc)[:200], 'artifacts_retained': True}, ensure_ascii=True))
        raise SystemExit(1)
