"""只读复核四档共同边界实验，组合既有数据审计与标定、冻结值验算。"""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

args = arguments()
assert set(args) == {'revision', 'margin_peers'}
revision = args['revision']
assert re.fullmatch('[0-9a-f]{40}', revision)
margin_peers = args['margin_peers']
assert isinstance(margin_peers, list) and margin_peers == sorted(set(margin_peers))
assert all(type(peer) is int and peer in (65536, 262144) for peer in margin_peers)
root = '/opt/sim/alltoall-group-schedule-' + revision[:8] + '-20261118'
source = Path(__file__).with_name('boundary_audit_remote.py').read_text()
source = source.replace('BOUNDARY_ROOT_VALUE', repr(root)).replace('BOUNDARY_REV_VALUE', repr(revision))
source = source.replace('MARGIN_PEERS_VALUE', repr(margin_peers))
source = source.replace('AUDIT_SOURCE_VALUE', repr(Path(__file__).with_name('audit_remote.py').read_text()))
in_container(source, 1800)
