"""Run one full launch using the pinned single-workspace FP32 object."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parent
shared = root / 'ht_parallel_unified'
support = root / 'ht_t32_e2e'
sys.path[:0] = [str(support), str(root / 'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST

revision = '186dfa18a0358add6af8ef237ebca23edffa86f5'
entry = 'tilexr_ht_pipeline_sst'
options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ['--args-file'] else {}
tokens = options.get('tokens', 4)
assert tokens in (4, 32)
case = f'ht-tunable-workspace-186dfa18-bs{tokens}-fp32'
timeout = 1200 if tokens == 4 else 6000
prefix = (root / 'inter_server/run_mte_t4_remote.py').read_text().rsplit('if __name__ == ', 1)[0]
if options.get('status'):
    source = prefix + f'\ncase = MOUNT / {case!r}\n' + r'''
report = {'exists': case.is_dir()}
for name in ('run.json', 'unified-result.json', 'phase-summary.json'):
    path = case / name
    if path.exists():
        data = json.loads(path.read_text())
        report[name] = {key: data[key] for key in ('status', 'returncode', 'runner_exit_code', 'sim_us',
                            'kernel_sha256', 'guards', 'checked', 'exports_sha256', 'sst_wall_seconds',
                            'runner_wall_seconds', 'cqe') if key in data}
log = case / 'sim.log'
if log.exists():
    raw = log.read_bytes()
    records = [int(match) for match in re.findall(rb'Sim:\s*(\d+)[^\n]*WrCQEResp', raw)]
    report['sim_log'] = {'bytes': len(raw), 'cqe': raw.count(b'WrCQEResp'),
                         'last_cqe_stamp': max(records, default=None)}
checks = case / 'check.log'
if checks.exists():
    for line in checks.read_text(errors='replace').splitlines():
        if line.startswith('{"ranks":'):
            report['check'] = json.loads(line)
            break
print(json.dumps(report, separators=(',', ':')))
'''
else:
    data = (here / 'pipeline.o.gz').read_bytes()
    binary_sha = '45982fb6c494379d20429d4801b66a51cd98c65afed80790d0b458744b8f3899'
    assert hashlib.sha256(gzip.decompress(data)).hexdigest() == binary_sha
    plans = (here / f'plans-t{tokens}.zip').read_bytes()
    exports = (support / 'exports.py').read_text()
    fields = ('gather', 'gather_ready', 'partial', 'ready', 'local_ready', 'returned', 'completed', 'output', 'status')
    exports = exports.replace('("returned", "completed", "status", "output")', repr(fields)).replace(
        '{"returned", "completed", "status", "output"}', repr(set(fields)))
    checker = (shared / 'check.py').read_text().replace('reduce=(32,8,4)', 'reduce=(96,8,4)')
    request = dict(revision=revision, object=base64.b64encode(data).decode(),
                   plans=base64.b64encode(plans).decode(), plans_sha256=hashlib.sha256(plans).hexdigest(),
                   exports=exports, checker=checker, loader=(root / 'ht_shared_ready/loader.py').read_text())
    prepare = (support / 'prepare.py').read_text().replace(
        '"tilexr_moonep_combine_ht_kernel"}', f'"{entry}"}}').replace('chip["block_dim"] == 16', 'chip["block_dim"] == 48')
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
    phase = (root / 'ht_four_stage/local_phases.py').read_text().replace('"--timeout", "540"', f'"--timeout", "{timeout}"').replace(
        'timeout=600)', f'timeout={timeout + 90})')
    modules = {'config': (shared / 'config.py').read_text(), 'route': (shared / 'route.py').read_text(),
               'shared_fixture': (shared / 'fixture.py').read_text(),
               'runtime': (here / 'runtime.py').read_text()}
    fixture = (here / 'fixture.py').read_text()
    fixture = 'import struct\n\n' + fixture[fixture.index('ENTRY = '):]
    source = '\n'.join((
        prefix, prepare, phase,
        f'for name, body in {modules!r}.items():\n'
        '    module = type(sys)(name)\n'
        '    if name == "route":\n'
        '        module.__name__ = "single_workspace_routes"\n'
        '    exec(compile(body, name, "exec"), module.__dict__)\n'
        '    globals()[name] = module\n',
        fixture, f'CASE = {case!r}\nKERNEL_SHA256 = {binary_sha!r}\n'
        f'TOKENS = {tokens}\nHIDDEN = 128\nCHUNKS = 4\n',
        (shared / 'green.py').read_text().split('\ndef main(', 1)[0].replace('    import runtime\n',''),
        '''
from time import monotonic

def run(request):
    case = MOUNT / CASE
    assert not case.exists()
    support = MOUNT.parent / BUILD / 'source/tools/unitest/simulator/experiments/inter_server_send/sst'
    prepare(request, case, support)
    started = monotonic()
    result = execute_phase(case)
    result.update(revision=request['revision'], tokens=TOKENS, blocks=BLOCKS,
                  send_cores=16, qp_counts=[16, 16], sync='Window', rotation='Legacy',
                  reuse_gather=True, profiling=False)
    result['runner_wall_seconds'] = round(monotonic() - started, 3)
    record = json.loads((case / 'run.json').read_text())
    if 'sst_wall_seconds' in record:
        result['sst_wall_seconds'] = round(record['sst_wall_seconds'], 3)
    if 'checked' not in result and (case / 'check.log').is_file():
        for line in (case / 'check.log').read_text(errors='replace').splitlines():
            if line.startswith('{"ranks":'):
                result['checked'] = json.loads(line)
                break
    write_json(case / 'unified-result.json', result)
    print(json.dumps(result, separators=(',', ':')))
''',
        f'''
try:
    run({request!r})
except Exception as error:
    import traceback
    last = traceback.extract_tb(error.__traceback__)[-1]
    print(json.dumps(dict(error_type=type(error).__name__, function=last.name, line=last.lineno)))
'''))
compile(source, '<single-workspace>', 'exec')
if options.get('check_only'):
    print(json.dumps({'source_bytes': len(source), 'case': case}))
    raise SystemExit

ssh = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '-o',
       'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1', '-o',
       'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no', HOST, 'python3', '-']
with askpass_environment() as env:
    result = subprocess.run(ssh, input=source.encode(), env=env, capture_output=True,
                            timeout=90 if options.get('status') else timeout + 250)
if result.returncode:
    print(json.dumps({'ssh_exit': result.returncode, 'stderr_bytes': len(result.stderr)}))
else:
    print(json.dumps(json.loads(result.stdout), separators=(',', ':')))
