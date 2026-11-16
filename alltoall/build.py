"""Stage an exact rebased TileXR revision and compile all three SST objects.
归档精确的 rebase 提交，构建三份独立仿真对象并绑定新布局。
"""
import base64
from pathlib import Path
import subprocess
from common import arguments, in_container, REV, ROOT, OLD
arguments()
repo='D:/workspace/TileXR'
subprocess.run(['git','-C',repo,'fetch','origin','codex/alltoall-mem-performance'],check=True,capture_output=True,timeout=120)
assert subprocess.check_output(['git','-C',repo,'rev-parse',REV],text=True).strip()==REV
archive=base64.b64encode(subprocess.check_output(['git','-C',repo,'archive',REV,'src','cmake','tests/collectives/memory','tests/relay/mock','tools/unitest'])).decode()
remote=Path(__file__).with_name('build_remote.py').read_text()
remote=remote.replace('REV_VALUE',repr(REV)).replace('ROOT_VALUE',repr(ROOT)).replace('OLD_VALUE',repr(OLD)).replace('ARCHIVE_VALUE',repr(archive))
in_container(remote,480)
