"""Install the reference platform in the task workspace after verifying its file manifest."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


def unpack(archive, output):
    with tarfile.open(archive, 'r:gz') as package:
        members = package.getmembers()
        assert len(members) <= 41
        manifest = json.load(package.extractfile('.manifest.json'))
        assert set(manifest) == {member.name for member in members} - {'.manifest.json'}
        output.mkdir()
        for member in members:
            relative = PurePosixPath(member.name)
            assert member.isfile() and not relative.is_absolute() and '..' not in relative.parts
            if member.name == '.manifest.json':
                continue
            assert member.size <= 4*1024*1024
            data = package.extractfile(member).read()
            assert hashlib.sha256(data).hexdigest() == manifest[member.name]
            dest = output/relative
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(data)
        (output/'.manifest.json').write_text(json.dumps(manifest,indent=2))
