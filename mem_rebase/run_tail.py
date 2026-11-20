"""Run the direct-entry and common-boundary batches serially on one candidate."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alltoall.common import arguments

args = arguments()
assert set(args) == {'revision'}
def read_large(config):
    command = [sys.executable, str(Path(__file__).with_name('batch_status.py')),
               '--args-file', str(config)]
    read = subprocess.run(command, capture_output=True, text=True, check=True, timeout=75)
    outer = json.loads(read.stdout)
    reply = json.loads(outer['stdout'])
    assert outer['ssh_exit'] == reply['container_exit'] == 0
    return json.loads(reply['stdout'])


# The three variants use one simulator environment. Wait for the previous
# batch's terminal receipt before starting either subsequent batch.
with tempfile.TemporaryDirectory(prefix='tilexr-mem-wait-') as root:
    config = Path(root) / 'args.json'
    config.write_text(json.dumps(dict(args, batch='memory-large')))
    deadline = time.monotonic() + 4800
    while True:
        prior = read_large(config)
        if prior['status'] == 'completed':
            assert prior['runtime_commit'] == args['revision'] and prior['case_count'] == 13
            assert len(prior['records']) == 13
            assert all(row['remaining_processes'] == [] for row in prior['records'])
            break
        if prior['status'] not in ('no_batch_receipt', 'running'):
            print(json.dumps(dict(status='preceding_batch_failed', batch='memory-large',
                                  completed=prior.get('cases_seen', len(prior.get('records', []))))))
            raise SystemExit(1)
        if time.monotonic() >= deadline:
            print(json.dumps(dict(status='preceding_batch_timeout', batch='memory-large')))
            raise SystemExit(1)
        time.sleep(30)

results = [prior]
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
