"""Read the retained build receipt and bounded diagnostics for a rebased candidate."""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments, in_container

args = arguments()
assert set(args) == {'revision', 'build_task'}
assert re.fullmatch('[0-9a-f]{40}', args['revision'])
assert re.fullmatch(r'mem-main-rebase-c266-build-[0-9a-f]{8}', args['build_task'])
receipt = Path.cwd()/('transport-'+args['build_task']+'.json')
if receipt.is_file():
    saved = json.loads(receipt.read_text())
    payload = json.loads(saved['stdout'].splitlines()[0])
    container = json.loads(payload['stdout'])
    print(json.dumps(dict(status='saved_transport', runner_exit=saved['returncode'],
                          container_exit=container['container_exit'], stdout=container['stdout'][:15000])))
else:
    root = '/opt/sim/alltoall-mem-main-rebase-' + args['revision'][:12]
    source = """import json
from pathlib import Path
root = Path(ROOT_VALUE)
summary = root/'build-summary.json'
logs = {}
for name in ('configure.log', 'compile.log'):
    path = root/name
    if path.is_file():
        logs[name] = dict(bytes=path.stat().st_size, tail=path.read_text(errors='replace').splitlines()[-8:])
print(json.dumps(dict(status='read_only', exists=root.is_dir(), logs=logs,
                      build=json.loads(summary.read_text()) if summary.is_file() else None)))
"""
    in_container(source.replace('ROOT_VALUE', repr(root)), 45)
