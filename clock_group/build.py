"""归档已推送的精确TileXR提交，通过既有桥接构建独立时钟和原双group对象。"""
import base64
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

args = arguments()
revision = args['revision']
assert re.fullmatch('[0-9a-f]{40}', revision)
repo = 'D:/workspace/TileXR'
subprocess.run(['git', '-C', repo, 'fetch', 'origin', 'codex/alltoall-mem-performance'],
               check=True, capture_output=True, timeout=120)
assert subprocess.check_output(['git', '-C', repo, 'rev-parse', revision], text=True).strip() == revision
subprocess.run(['git', '-C', repo, 'merge-base', '--is-ancestor', revision,
                'origin/codex/alltoall-mem-performance'], check=True, capture_output=True)
archive = base64.b64encode(subprocess.check_output(['git', '-C', repo, 'archive', revision,
    'src', 'cmake', 'tests/collectives/memory', 'tests/relay/mock', 'tools/unitest'])).decode()
root = '/opt/sim/alltoall-clock-group-' + revision[:8] + '-20261117'
remote = Path(__file__).with_name('build_remote.py').read_text()
remote = remote.replace('REV_VALUE', repr(revision)).replace('ROOT_VALUE', repr(root))
remote = remote.replace('ARCHIVE_VALUE', repr(archive))
in_container(remote, 540)
