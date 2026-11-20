"""Read one batch receipt even while its simulator process is active."""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import CONTAINER, arguments, ssh

args = arguments()
assert set(args) == {'revision', 'batch'}
assert re.fullmatch('[0-9a-f]{40}', args['revision'])
assert args['batch'] in ('memory-small-medium', 'memory-large', 'direct', 'boundary')
root = '/opt/sim/alltoall-mem-main-rebase-' + args['revision'][:12]
reader = """
import json
from pathlib import Path
root = Path(ROOT_VALUE)
path = root / ('batch-' + BATCH_VALUE + '.json')
if not path.is_file():
    print(json.dumps(dict(status='no_batch_receipt')))
else:
    result = json.loads(path.read_text())
    if result['status'] in ('running', 'completed'):
        records = result['records']
        summary = {k: v for k, v in result.items() if k != 'records'}
        summary.update(cases_seen=len(records), validated=sum(r['validated'] for r in records))
        if result['status'] == 'completed':
            summary['records'] = records
        print(json.dumps(summary, separators=(',', ':')))
    else:
        print(json.dumps(result, separators=(',', ':')))
""".replace('ROOT_VALUE', repr(root)).replace('BATCH_VALUE', repr(args['batch']))
host = """
import json
import subprocess
data = json.loads(subprocess.check_output(['docker', 'inspect', CONTAINER_VALUE], text=True))[0]
assert data['Id'] == '21b762bee47b5b0302043652e6880c55763d5afedd247568185310d37a010198'
assert data['State']['Running']
p = subprocess.run(['docker', 'exec', '-i', CONTAINER_VALUE, 'python3', '-'],
                   input=READER_VALUE, text=True, capture_output=True, timeout=20)
print(json.dumps(dict(container_exit=p.returncode, stdout=p.stdout,
                      error_tail=p.stderr.splitlines()[-4:])))
assert p.returncode == 0
""".replace('CONTAINER_VALUE', repr(CONTAINER)).replace('READER_VALUE', repr(reader))
ssh(host, 45)
