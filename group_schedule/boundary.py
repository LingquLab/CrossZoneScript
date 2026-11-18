"""串行执行单一数据量的一段标定/复验，固定来源并保存全部样本。"""
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments,in_container

args=arguments()
assert set(args)=={'revision','peer_bytes','stage'}
assert re.fullmatch('[0-9a-f]{40}',args['revision'])
assert args['peer_bytes'] in (512,8192,65536,262144)
assert args['stage'] in ('baseline','bootstrap','verify','late-entry','margin-bootstrap','margin-verify')
assert args['stage']!='late-entry' or args['peer_bytes']==512
assert not args['stage'].startswith('margin-') or args['peer_bytes'] in (65536,262144)
root='/opt/sim/alltoall-group-schedule-'+args['revision'][:8]+'-20261118'
source=Path(__file__).with_name('boundary_remote.py').read_text()
case_source=Path(__file__).with_name('case_remote.py').read_text()
remote=source.replace('ROOT_VALUE',repr(root)).replace('ARGS_VALUE',repr(args))
remote=remote.replace('CASE_SOURCE_VALUE',repr(case_source))
in_container(remote,23000)
