"""Fetch and archive the immutable TileXR source for this SST run."""
import base64
import hashlib
import subprocess
import tempfile
from pathlib import Path

SESSION='598ffb8d-c53a-4736-b168-be6f3d0e706f'
REVISIONS = {'a50893f': 'a50893f1544b251af517ea8e77da391ec2072950'}


def source_request(candidate, revision, logs):
    with tempfile.TemporaryDirectory(prefix='combine-ht-source-') as temporary:
        def git(*argv):
            command = ['git', '-C', temporary, *argv]
            proc = subprocess.run(command, capture_output=True, timeout=180)
            if proc.returncode:
                (logs/'source-git.stderr').write_bytes(proc.stderr)
                raise RuntimeError('source_git_failed_see_green_log')
            return proc.stdout
        git('init', '-q')
        git('fetch', '--depth', '1', 'https://github.com/LingquLab/TileXR.git', revision)
        assert git('rev-parse', 'FETCH_HEAD').decode().strip() == revision
        tree = git('rev-parse', 'FETCH_HEAD^{tree}').decode().strip()
        archive = git('archive', '--format=tar.gz', revision)
    directory = Path(__file__).parent
    return {
        'candidate': candidate,
        'revision': revision,
        'tree': tree,
        'source_sha256': hashlib.sha256(archive).hexdigest(),
        'source': base64.b64encode(archive).decode(),
        'profile_files': {name: (directory/name).read_text(encoding='utf-8')
                          for name in ('build_profile.py', 'profile_capture.h')},
    }
