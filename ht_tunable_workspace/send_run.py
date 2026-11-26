"""Dispatch a single pinned standalone-send case to the green SST runner."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parent
sys.path[:0] = [str(root / 'ht_t32_e2e'), str(root / 'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST

options = json.loads(Path(sys.argv[2]).read_text())
assert (options['six'], options['two'], options['policy']) in {
    (8, 8, 'ServerStaggered'), (16, 8, 'ServerStaggered'), (8, 16, 'ServerStaggered'),
    (16, 16, 'ServerStaggered'), (16, 16, 'RankOffsetRing'), (16, 16, 'CustomOrder'),
}
timeout = 1800
entry = 'tilexr_ep_ht_inter_server_send_kernel'
revision = '186dfa18a0358add6af8ef237ebca23edffa86f5'
digest = 'b1c982a147a559375ea883919516e4b7c3092ebb9d6716b42f961cbb61f995ec'

route = (root / 'ht_peer_order/fixture.py').read_text().replace(
    'def generate_inputs(tokens, seed, abis, modules):\n    native, runtime = modules',
    'def generate_inputs(tokens, seed, abis, runtime):').replace('native.config_values', 'sender_config').replace(
    'tokens, 4, 256, MAGIC', 'tokens, 4, 128, MAGIC').replace('status=native.status_image(), ', '')
checker = (root / 'ht_four_stage/check_remote.py').read_text().replace(
    '(bytes(4) + b"\\xa5" * 28) * 16 if field == "status" else source.read_bytes()', 'source.read_bytes()')
prepare = (root / 'ht_t32_e2e/prepare.py').read_text().replace(
    '"tilexr_moonep_combine_ht_kernel"}', f'"{entry}"}}').replace('chip["block_dim"] == 16', 'chip["block_dim"] == 8')
prepare = prepare.replace(
    'target.write_text(original.replace(old_entry, \'manifest["entry"] != \' + json.dumps(build["entry"])))',
    '''runner = original.replace(old_entry, 'manifest["entry"] != ' + json.dumps(build["entry"]))
            before = "            child = subprocess.Popen("
            after = "            code = child.wait(timeout=timeout)"
            assert runner.count(before) == runner.count(after) == 1
            runner = runner.replace(before, "            from time import monotonic\\n            sst_started = monotonic()\\n" + before)
            runner = runner.replace(after, after + "\\n            record['sst_wall_seconds'] = monotonic() - sst_started")
            compile(runner, '<timed-sst-adapter>', 'exec')
            target.write_text(runner)''')
phase = (root / 'ht_four_stage/local_phases.py').read_text().replace(
    '"--timeout", "540"', f'"--timeout", "{timeout}"').replace('timeout=600)', f'timeout={timeout + 90})')
prefix = (root / 'inter_server/run_mte_t4_remote.py').read_text().rsplit('if __name__ == ', 1)[0]
request = dict(options, revision=revision, sha256=digest,
               runtime=(here / 'runtime.py').read_text(), loader=(root / 'ht_shared_ready/loader.py').read_text(),
               checker=checker, exports=(root / 'ht_t32_e2e/exports.py').read_text())
read_only = options.get('status') or options.get('cq')
if not read_only:
    obj = (here / 'inter-server-send.o.gz').read_bytes()
    assert hashlib.sha256(gzip.decompress(obj)).hexdigest() == digest
    request.update(object=base64.b64encode(obj).decode(), plans=base64.b64encode((here / 'send-plans.zip').read_bytes()).decode())
source = '\n'.join((prefix, prepare, phase, (root / 'ht_parallel_unified/config.py').read_text(),
                    route, (here / 'send_only.py').read_text(),
                    (root / 'ht_parallel_unified/queue_trace.py').read_text(), (here / 'cq.py').read_text(),
                    (here / 'send_installer.py').read_text(), f'''
try:
    print(json.dumps(run_send({request!r}), separators=(',', ':')))
except Exception as error:
    import traceback
    last = traceback.extract_tb(error.__traceback__)[-1]
    print(json.dumps(dict(error_type=type(error).__name__, function=last.name, line=last.lineno)))
'''))
compile(source, '<tunable-send-only>', 'exec')
if options.get('check_only'):
    print(json.dumps({'source_bytes': len(source)}))
    raise SystemExit

ssh = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '-o', 'BatchMode=no',
       '-o', 'NumberOfPasswordPrompts=1', '-o', 'PreferredAuthentications=password', '-o',
       'PubkeyAuthentication=no', HOST, 'python3', '-']
with askpass_environment() as env:
    result = subprocess.run(ssh, input=source.encode(), env=env, capture_output=True,
                            timeout=120 if read_only else timeout + 250)
if result.returncode:
    print(json.dumps({'ssh_exit': result.returncode, 'stderr_bytes': len(result.stderr)}))
else:
    print(json.dumps(json.loads(result.stdout), separators=(',', ':')))
