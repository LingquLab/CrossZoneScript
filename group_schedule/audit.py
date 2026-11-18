"""复核保存的64P调度实验：源码、对象、动态库、数据和4096条状态。"""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

args = arguments(); revision, labels = args['revision'], args['labels']
assert re.fullmatch('[0-9a-f]{40}', revision)
assert 1 <= len(labels) <= 6 and len(set(labels)) == len(labels)
assert all(re.fullmatch('[a-z0-9-]{1,60}', label) for label in labels)
root = '/opt/sim/alltoall-group-schedule-'+revision[:8]+'-20261118'
script = Path(__file__).with_name('audit_remote.py').read_text()
script = script.replace('ROOT_VALUE', repr(root)).replace('REV_VALUE', repr(revision))
in_container(script.replace('LABELS_VALUE', repr(labels)), 600)
