"""Fetch and archive the immutable TileXR source for this SST run."""
import base64
import hashlib
import subprocess
import tempfile
from pathlib import Path

SESSION='5c53b5f4-6259-4c0d-8a3b-cde264d9cdcb'
REVISIONS = {'4d37ae6': '4d37ae67cf2f6baa06b3efbe65c71649a6f24203'}


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
