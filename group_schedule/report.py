"""按蓝区冻结的标签和时钟参数汇总正式复测，另列预热与全矩阵成功数。"""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

args = arguments()
revision, groups = args['revision'], args['groups']
assert re.fullmatch('[0-9a-f]{40}', revision)
assert len(groups) == 3 and {g['peer_bytes'] for g in groups} == {512, 8192, 65536}
labels = []
for group in groups:
    assert len(group['warmup']) == 4 and len(group['rounds']) == 3
    assert all(len(row) == 4 for row in group['rounds'])
    labels.extend(group['warmup'])
    labels.extend(label for row in group['rounds'] for label in row)
assert len(labels) == len(set(labels)) == 48
assert all(re.fullmatch('[a-z0-9-]{1,60}', label) for label in labels)
root = '/opt/sim/alltoall-group-schedule-' + revision[:8] + '-20261118'
source = Path(__file__).with_name('report_remote.py').read_text()
source = source.replace('ROOT_VALUE', repr(root)).replace('REV_VALUE', repr(revision))
in_container(source.replace('GROUPS_VALUE', repr(groups)), 180)
