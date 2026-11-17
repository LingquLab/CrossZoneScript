"""Prepare a fresh build from verified saved source plus exact spdlog gitlink."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import stat
import subprocess
import sys
import zipfile

OLD = Path('/root/tilelang-107-general-64ed5828-20261117-run01')
ROOT = Path('/root/tilelang-107-general-64ed5828-20261117-run02')
ORIGINAL_SHA = '87832d7036715e973a53fd4a71f22debd56c4332269ee5ef2ba424c26b8e7416'
OVERLAY_SHA = 'a85ea076e4ab51a6f295a4eec0fe68c7bd1d587b19bad0582de90e8769cd6801'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_members(archive):
    assert archive.testzip() is None
    names = archive.namelist()
    assert len(names) == len(set(names)) and sum(i.file_size for i in archive.infolist()) < 40 * 1024**2
    for info in archive.infolist():
        name = PurePosixPath(info.filename)
        assert not name.is_absolute() and '..' not in name.parts and not stat.S_ISLNK(info.external_attr >> 16)


def populate(old, root):
    original = old / 'payload.zip'; overlay = root / 'build02-overlay.zip'
    assert sha(original.read_bytes()) == ORIGINAL_SHA and sha(overlay.read_bytes()) == OVERLAY_SHA
    assert {p.name for p in root.iterdir()} == {'transfer-started.json', 'build02-overlay.zip'}
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(overlay) as repair:
        safe_members(source); safe_members(repair)
        manifest = json.loads(source.read('payload-manifest.json'))
        final = json.loads(repair.read('payload-manifest.json'))
        assert set(source.namelist()) == set(manifest['files']) | {'payload-manifest.json'}
        assert set(repair.namelist()) == {'operations.py', 'build_remote.py', 'phase_parent.py', 'spdlog-source.zip', 'payload-manifest.json'}
        assert final['original_payload_sha256'] == ORIGINAL_SHA and final['spdlog_commit'] == 'd5275e3343c4dca4feb9fa772199181509f2a657'
        assert final['candidate'] == manifest['candidate'] == '64ed582898e1321a0e93a2c4e0e78a6deb2f4868'
        for name, expected in manifest['files'].items():
            assert sha(source.read(name)) == expected
        files = {name: source.read(name) for name in manifest['files']}
        files.update({name: repair.read(name) for name in repair.namelist() if name != 'payload-manifest.json'})
        assert set(files) == set(final['files'])
        for name, raw in files.items():
            assert sha(raw) == final['files'][name]
        for name, raw in files.items():
            destination = root / name; destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('xb') as stream:
                stream.write(raw)
        (root / 'payload-manifest.json').write_bytes(repair.read('payload-manifest.json'))


def main():
    assert os.geteuid() == 0 and sha(Path('/etc/machine-id').read_bytes()) == '78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'
    saved = OLD / 'build-summary.json'
    assert sha(saved.read_bytes()) == '9dd4cb6f5a63f4ba4e4eb4ef1bb9219c693d3e073b95ce6aaf4cd3f00db72426'
    failure = json.loads(saved.read_text())
    assert failure['status'] == 'FAIL_OR_UNVERIFIED' and failure['npu_launch'] is False and failure['error_type'] == 'FileNotFoundError'
    if sys.argv[1] == 'prepare':
        ROOT.mkdir(mode=0o750, exist_ok=False)
        (ROOT / 'transfer-started.json').write_text(json.dumps({'candidate': failure['candidate'], 'original_build_summary_sha256': sha(saved.read_bytes()), 'npu_launch': False}) + '\n')
        print(json.dumps({'status': 'PREPARED', 'npu_launch': False}))
        return 0
    assert sys.argv[1] == 'build'
    populate(OLD, ROOT)
    process = subprocess.run(['/usr/bin/python3', str(ROOT / 'build_remote.py')], cwd=ROOT, capture_output=True, timeout=850)
    (ROOT / 'build-parent.stdout.log').write_bytes(process.stdout)
    (ROOT / 'build-parent.stderr.log').write_bytes(process.stderr)
    (ROOT / 'build-parent.receipt.json').write_text(json.dumps({'exit_code': process.returncode}) + '\n')
    if process.returncode:
        print(json.dumps({'status': 'FAIL', 'npu_launch': False, 'saved_summary': str(ROOT / 'build-summary.json')}))
        return process.returncode
    result = json.loads((ROOT / 'build-summary.json').read_text())
    assert result['status'] == 'PASS' and result['npu_launch'] is False
    result.update(original_failure_preserved=True, spdlog_commit='d5275e3343c4dca4feb9fa772199181509f2a657', repair_scope='Build source dependency only')
    print(json.dumps(result, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
