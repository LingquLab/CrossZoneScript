"""Freeze one screened phase target, then compare five fresh pairs with SyncAll."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

args = arguments()
assert set(args) == {'revision', 'target'}
assert args['revision'] == '347747af83daa7d3054563626d4dbef85a0ea729'
assert type(args['target']) is int and 64000 <= args['target'] <= 66367
root = '/opt/sim/alltoall-group-schedule-' + args['revision'][:8] + '-20261118'
source = Path(__file__).with_name('gap_compare_remote.py').read_text()
source = source.replace('COMPARE_ROOT_VALUE', repr(root)).replace('COMPARE_ARGS_VALUE', repr(args))
source = source.replace('COMPARE_CASE_SOURCE_VALUE', repr(Path(__file__).with_name('case_remote.py').read_text()))
source = source.replace('COMPARE_AUDIT_SOURCE_VALUE', repr(Path(__file__).with_name('audit_remote.py').read_text()))
in_container(source, 22000)
