"""Extract only this task's verified source archive into a new revision directory."""
import hashlib
import json
import pathlib
import sys
import tarfile
import uuid

alias, commit, expected_sha, archive_path = sys.argv[1:]
assert alias in ('ma-0', 'ma-1', 'ma-2')
assert len(commit) == 40 and all(c in '0123456789abcdef' for c in commit)
archive = pathlib.Path(archive_path)
actual_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
assert actual_sha == expected_sha, 'Archive checksum mismatch'
parent = pathlib.Path.home() / 'work' / 'pangu_sophon_pytorch' / alias
parent.mkdir(parents=True, exist_ok=True)
target = parent / commit
marker = target / '.cross-zone-deployment.json'
if target.exists():
    assert marker.is_file(), 'Existing destination is not owned by this deployment; preserved'
    existing = json.loads(marker.read_text())
    assert existing['commit'] == commit and existing['archive_sha256'] == expected_sha
    action = 'verified_existing'
    count = existing['file_count']
else:
    stage = parent / ('.staging-' + uuid.uuid4().hex)
    stage.mkdir()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        assert all(not m.islnk() and not m.isdev() for m in members), 'Unsupported archive member'
        tar.extractall(stage, filter='data')
        count = sum(m.isfile() for m in members)
        for member in members:
            if member.isfile():
                extracted = stage / member.name
                assert extracted.is_file() and extracted.stat().st_size == member.size
    assert (stage / 'scripts' / 'example').is_dir()
    metadata = {'commit':commit, 'archive_sha256':expected_sha, 'file_count':count}
    (stage / marker.name).write_text(json.dumps(metadata, indent=2) + '\n')
    assert not target.exists(), 'Destination appeared concurrently; preserved'
    stage.rename(target)
    action = 'deployed'
print(json.dumps({'action':action,'path':str(target),'commit':commit,'archive_sha256':actual_sha,'file_count':count}))
