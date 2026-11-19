"""Screen one fixed 4MiB/rank phase target with three fresh SST calls."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

args = arguments()
assert set(args) == {'revision', 'target'}
assert args['revision'] == '347747af83daa7d3054563626d4dbef85a0ea729'
assert type(args['target']) is int and 64000 <= args['target'] <= 66367
root = '/opt/sim/alltoall-group-schedule-' + args['revision'][:8] + '-20261118'
source = Path(__file__).with_name('gap_search_remote.py').read_text()
source = source.replace('ROOT_VALUE', repr(root)).replace('ARGS_VALUE', repr(args))
source = source.replace('CASE_SOURCE_VALUE', repr(Path(__file__).with_name('case_remote.py').read_text()))
in_container(source, 6500)
