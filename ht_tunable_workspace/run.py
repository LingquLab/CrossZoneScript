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
from sync_layout import chunk_ends, sync_steps

revision = '186dfa18a0358add6af8ef237ebca23edffa86f5'
entry = 'tilexr_ht_pipeline_sst'
options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ['--args-file'] else {}
tokens = options.get('tokens', 4)
assert tokens in (4, 32, 1024)
mode = options.get('mode')
profile = bool(options.get('profile'))
assert mode in (None, 'direct', 'logical', 'physical')
assert tokens != 4 or (mode in (None, 'direct') and not profile)
assert tokens != 1024 or (mode != 'direct' and profile)
assert not (profile and mode == 'direct')
case = f'ht-tunable-workspace-186dfa18-bs{tokens}-fp32' + (f'-{mode}' if mode else '') + ('-profile' if profile else '')
ends = chunk_ends(tokens)
step_ends = [ends[index] for index in sync_steps(tokens, mode)]
timeout = {4: 1200, 32: 6000, 1024: 54000}[tokens]
prefix = (root / 'inter_server/run_mte_t4_remote.py').read_text().rsplit('if __name__ == ', 1)[0]
if options.get('cq') or options.get('direct_activity'):
    evidence = (shared / 'queue_trace.py').read_text().split('\ndef queue_trace(', 1)[0]
    if options.get('direct_activity'):
        assert mode == 'direct'
        summary_command = 'result = direct_activity(case)'
    else:
        summary_command = (f'result = count_candidate_cq(case, {tokens}, mode={mode!r})\n'
                           f"if result['first_missing']: result['arrival'] = pipeline_missing_arrivals(case, {tokens}, {mode!r}, result['first_missing'])")
    source = '\n'.join((prefix, (shared / 'config.py').read_text(), evidence, (here / 'sync_layout.py').read_text(),
                        (here / 'cq.py').read_text(),
                        f'case = MOUNT / {case!r}', summary_command,
                        "print(json.dumps(result, separators=(',', ':')))"))
elif options.get('read_profile'):
    assert profile
    source = '\n'.join((prefix, (here / 'profile.py').read_text(), f'case = MOUNT / {case!r}',
                        f'result = extract_pipeline_profile(case, 48, 8, 16, {step_ends!r}, {len(ends)})',
                        "print(json.dumps(result, separators=(',', ':')))"))
elif options.get('status'):
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
    plans = (here / (f'plans-t{tokens}' + (f'-{mode}' if mode else '') + '.zip')).read_bytes()
    exports = (support / 'exports.py').read_text()
    fields = ('gather', 'gather_ready', 'partial', 'ready', 'local_ready', 'returned', 'completed', 'output', 'status')
    export_fields = fields + ('profile',) if profile else fields
    exports = exports.replace('("returned", "completed", "status", "output")', repr(export_fields)).replace(
        '{"returned", "completed", "status", "output"}', repr(set(export_fields)))
    checker = (shared / 'check.py').read_text().replace('reduce=(32,8,4)', 'reduce=(96,8,4)')
    if mode == 'direct':
        checker = checker.replace('cross_return=(512,16,32)', 'cross_return=(512,8,32)')
    loader = (root / 'ht_shared_ready/loader.py').read_text()
    if tokens == 1024:
        loader = loader.replace('    if len(ranks) != 64 or {row["rank"] for row in ranks} != set(range(64)):\n'
                                '        raise ValueError("Expected all 64 logical ranks")\n', '')
        loader = loader.replace('for row in sorted(ranks, key=lambda x: x["rank"]):\n        rank = row["rank"]',
                                'for rank, row in enumerate(ranks):\n        assert rank == row["rank"]')
        loader = loader.replace('    (output / "workload.yml").write_text(',
                                '    assert len(workload) == 64\n    (output / "workload.yml").write_text(')
    request = dict(revision=revision, object=base64.b64encode(data).decode(),
                   plans=base64.b64encode(plans).decode(), plans_sha256=hashlib.sha256(plans).hexdigest(),
                   exports=exports, checker=checker, loader=loader)
    prepare = (support / 'prepare.py').read_text().replace(
        '"tilexr_moonep_combine_ht_kernel"}', f'"{entry}"}}').replace('chip["block_dim"] == 16', 'chip["block_dim"] == 48')
    if tokens == 1024:
        prepare = prepare.replace('in (4, 16, 32)', 'in (4, 16, 32, 1024)').replace(
            'sum(member.size for member in members) < 512 * 1024 * 1024',
            'sum(member.size for member in members) < 5 * 1024**3')
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
    green = (shared / 'green.py').read_text().split('\ndef main(', 1)[0].replace('    import runtime\n','')
    green = green.replace('(native,runtime))',
                          "(native,runtime), plans.read(f'{rank}/send.bin') if MODE == 'direct' else None, PROFILE)")
    if tokens == 1024:
        stream = (here / 'stream_fixture.txt').read_text()
        start = green.index('        ranks,trace=')
        stop = green.index("        write_json(golden/'trace-offsets.json'", start)
        green = green[:start] + stream + green[stop:]
    source = '\n'.join((
        prefix, prepare, phase,
        f'for name, body in {modules!r}.items():\n'
        '    module = type(sys)(name)\n'
        '    if name == "route":\n'
        '        module.__name__ = "single_workspace_routes"\n'
        '    exec(compile(body, name, "exec"), module.__dict__)\n'
        '    globals()[name] = module\n',
        fixture, (here / 'profile.py').read_text(), (here / 'sync_layout.py').read_text(),
        f'CASE = {case!r}\nKERNEL_SHA256 = {binary_sha!r}\n'
        f'TOKENS = {tokens}\nHIDDEN = 128\nCHUNKS = {len(ends)}\nMODE = {mode!r}\nPROFILE = {profile!r}\n',
        green,
        '''
from time import monotonic

def run(request):
    case = MOUNT / CASE
    assert not case.exists()
    support = MOUNT.parent / BUILD / 'source/tools/unitest/simulator/experiments/inter_server_send/sst'
    prepare(request, case, support)
    started = monotonic()
    result = execute_phase(case)
    direct = MODE == 'direct'
    result.update(revision=request['revision'], tokens=TOKENS, blocks=BLOCKS, hidden=HIDDEN, chunks=CHUNKS,
                  send_cores=8 if direct else 16, qp_counts=[8, 8] if direct else [16, 16],
                  sync={'logical': 'LogicalChunk', 'physical': 'PhysicalChunk'}.get(MODE, 'Window'),
                  rotation='ServerStaggered' if direct else 'Legacy', reuse_gather=not direct, profiling=PROFILE)
    if direct:
        trace = json.loads((case / 'fixture-data/trace-offsets.json').read_bytes())
        counts = {name: sum(len(row['sq_bindings'][name]) for row in trace) for name in trace[0]['sq_bindings']}
        result['sq_binding_counts'] = counts
        result['sq_address_overlap'] = 0
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
                            timeout=120 if any(options.get(key) for key in ('status', 'cq', 'direct_activity', 'read_profile')) else timeout + 250)
if result.returncode:
    print(json.dumps({'ssh_exit': result.returncode, 'stderr_bytes': len(result.stderr)}))
else:
    print(json.dumps(json.loads(result.stdout), separators=(',', ':')))
