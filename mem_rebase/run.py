"""Run one serial SST matrix batch against a previously compiled revision."""
from pathlib import Path
import re
import sys

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))
from alltoall.common import arguments, in_container

args = arguments()
assert set(args) == {'revision', 'batch'}
assert re.fullmatch('[0-9a-f]{40}', args['revision'])
assert args['batch'] in ('memory-small-medium', 'memory-large', 'direct', 'boundary')
root = '/opt/sim/alltoall-mem-main-rebase-' + args['revision'][:12]
source = Path(__file__).with_name('run_remote.py').read_text()
sources = {
    'memory': (repo/'alltoall/case_remote.py').read_text().replace(
        "'rebase_db76effb_'+label", "'mem_main_'+root.name.rsplit('-', 1)[-1]+'_'+label"),
    'clock': (repo/'clock_group/case_remote.py').read_text(),
    'boundary': (repo/'group_schedule/case_remote.py').read_text(),
}
source = source.replace('ARGS_VALUE', repr(args)).replace('ROOT_VALUE', repr(root))
source = source.replace('RUNNERS_VALUE', repr(sources))
in_container(source, 24000)
