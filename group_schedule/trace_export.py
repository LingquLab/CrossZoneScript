"""Read saved 4MiB/rank boundary timestamps; never launch a simulation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

args = arguments()
assert set(args) == {'label'}
assert args['label'] in ('boundary-65536-margin-local-cross-r2',
                         'boundary-65536-margin-sync-r1')
source = Path(__file__).with_name('trace_export_remote.py').read_text()
source = source.replace('TRACE_LABEL_VALUE', repr(args['label']))
in_container(source, 180)
