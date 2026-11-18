"""归档精确提交并构建匹配阶段诊断与原对象；不启动仿真。"""
import base64
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

revision = arguments()['revision']
assert re.fullmatch('[0-9a-f]{40}', revision)
repo = 'D:/workspace/TileXR'
subprocess.run(['git', '-C', repo, 'fetch', 'origin', 'codex/alltoall-mem-performance'],
               check=True, capture_output=True, timeout=120)
subprocess.run(['git', '-C', repo, 'merge-base', '--is-ancestor', revision,
                'origin/codex/alltoall-mem-performance'], check=True, capture_output=True)
archive = base64.b64encode(subprocess.check_output(['git', '-C', repo, 'archive', revision,
    'src', 'cmake', 'tests/collectives/memory', 'tests/relay/mock', 'tools/unitest'])).decode()
root = '/opt/sim/alltoall-group-phase-' + revision[:8] + '-20261118'
source = Path(__file__).with_name('build_remote.py').read_text()
source = source.replace('REV_VALUE', repr(revision)).replace('ROOT_VALUE', repr(root))
in_container(source.replace('ARCHIVE_VALUE', repr(archive)), 540)
