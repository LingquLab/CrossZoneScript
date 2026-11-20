"""Run the direct-entry and common-boundary batches serially on one candidate."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments

args = arguments()
assert set(args) == {'revision'}
results = []
runner = Path(__file__).with_name('run.py')
for batch in ('direct', 'boundary'):
    with tempfile.TemporaryDirectory(prefix='tilexr-mem-batch-') as root:
        config = Path(root) / 'args.json'
        config.write_text(json.dumps(dict(args, batch=batch)))
        run = subprocess.run([sys.executable, str(runner), '--args-file', str(config)],
                             capture_output=True, text=True, timeout=24400)
    transport = json.loads(run.stdout)
    container = json.loads(transport['stdout'])
    stdout = container['stdout']
    if run.returncode or transport['ssh_exit'] or container['container_exit'] or run.stderr:
        error = dict(status='runner_failed', batch=batch, completed=results,
                     stdout=stdout[-5000:], errors=container['error_tail'])
        print(json.dumps(error, separators=(',', ':')), flush=True)
        raise SystemExit(1)
    result = json.loads(stdout)
    assert result['batch'] == batch and result['runtime_commit'] == args['revision']
    assert result['status'] == 'completed'
    results.append(result)
print(json.dumps(dict(status='completed', runtime_commit=args['revision'],
                      main=results[0]['main'], batches=results), separators=(',', ':')))
