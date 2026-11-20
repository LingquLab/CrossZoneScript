"""Build the pinned AllToAll memory source from the validation branch."""
import base64
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

args = arguments()
assert set(args) == {'revision', 'main'}
assert all(re.fullmatch('[0-9a-f]{40}', value) for value in args.values())
repo = 'D:/workspace/TileXR'
branch = 'codex/alltoall-mem-rebase-c266c4f9'
revision = args['revision']
git = ['git', '-C', repo]
subprocess.run(git + ['fetch', 'origin', branch], check=True, capture_output=True, timeout=300)
subprocess.run(git + ['merge-base', '--is-ancestor', revision, 'origin/' + branch],
               check=True, capture_output=True, timeout=30)
subprocess.run(git + ['merge-base', '--is-ancestor', args['main'], revision],
               check=True, capture_output=True, timeout=30)
memory_dir = 'tests/collectives/memory'
files = subprocess.check_output(git + ['ls-tree', '-z', '--name-only', revision, memory_dir+'/'],
                                timeout=30).decode().strip('\0').split('\0')
archive = subprocess.check_output(git + ['archive', revision, 'include', 'src', 'cmake', 'tools/unitest',
    'tests/relay/mock'] + [name for name in files if not name.endswith('/artifacts')], timeout=120)
root = '/opt/sim/alltoall-mem-main-rebase-' + revision[:12]
source = Path(__file__).with_name('build_remote.py').read_text()
in_container(source.replace('ARGS_VALUE', repr(args)).replace('ROOT_VALUE', repr(root))
             .replace('ARCHIVE_VALUE', repr(base64.b64encode(archive).decode())), 1500)
