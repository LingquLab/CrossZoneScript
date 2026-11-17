"""审计指定已完成用例；不编译、不启动仿真，不覆盖原始结果。"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

args = arguments()
revision, labels = args['revision'], args['labels']
assert re.fullmatch('[0-9a-f]{40}', revision)
assert 1 <= len(labels) <= 12 and len(set(labels)) == len(labels)
assert all(re.fullmatch('[a-z0-9-]{1,60}', label) for label in labels)
root = '/opt/sim/alltoall-clock-group-' + revision[:8] + '-20261117'
query = Path(__file__).with_name('audit_remote.py').read_text()
query = query.replace('ROOT_VALUE', repr(root)).replace('REV_VALUE', repr(revision))
query = query.replace('LABELS_VALUE', repr(labels))
compile(query, 'clock-audit-remote.py', 'exec')
in_container(query, 240)
