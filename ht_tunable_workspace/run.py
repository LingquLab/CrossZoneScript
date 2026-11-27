"""Run one full launch using a pinned single-workspace object."""
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
bf16 = bool(options.get('bf16'))
tokens = options.get('tokens', 4)
assert tokens in (4, 32, 1024)
mode = options.get('mode')
profile = bool(options.get('profile'))
memory_retry = bool(options.get('memory_retry'))
assert mode in (None, 'direct', 'logical', 'physical')
assert tokens != 4 or (mode in (None, 'direct') and not profile)
assert tokens != 1024 or (mode != 'direct' and profile)
assert not (profile and mode == 'direct')
assert not memory_retry or tokens == 1024
assert not bf16 or (tokens == 4 and mode is None and not profile)
case = f'ht-tunable-workspace-186dfa18-bs{tokens}-fp32' + (f'-{mode}' if mode else '') + ('-profile' if profile else '')
if bf16:
    case = 'ht-tunable-workspace-186dfa18-bs4-bf16-castfix'
    entry = 'tilexr_moonep_combine_ht_pipeline_kernel'
if memory_retry:
    case += '-mem256'
ends = chunk_ends(tokens)
step_ends = [ends[index] for index in sync_steps(tokens, mode)]
timeout = 1800 if bf16 else {4: 1200, 32: 6000, 1024: 108000}[tokens]
adapter_margin = 1800 if tokens == 1024 else 90
transport_margin = 3600 if tokens == 1024 else 250
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
elif options.get('recover_profile'):
    assert profile
    source = '\n'.join((prefix, (root / 'ht_four_stage/local_phases.py').read_text(),
                        (here / 'profile.py').read_text(), (here / 'recover_profile.py').read_text(),
                        f'case = MOUNT / {case!r}', 'try:',
                        f'    result = recover_profile(case, {step_ends!r}, {len(ends)})',
                        "except (AssertionError, ValueError, OSError) as error:",
                        "    import traceback",
                        "    frame = traceback.extract_tb(error.__traceback__)[-1]",
                        "    result = dict(status='recovery_failed', error_type=type(error).__name__, detail=str(error), function=frame.name, line=frame.lineno)",
                        "print(json.dumps(result, separators=(',', ':')))"))
elif options.get('capacity'):
    source = prefix + r'''
import os
memory = Path('/proc/meminfo').read_text()
available_kib = next(int(line.split()[1]) for line in memory.splitlines() if line.startswith('MemAvailable:'))
print(json.dumps(dict(available_cpu_count=len(os.sched_getaffinity(0)), available_memory_kib=available_kib,
                      available_storage_bytes=shutil.disk_usage(MOUNT).free)))
'''
elif options.get('termination'):
    source = prefix + f'\ncase = MOUNT / {case!r}\n' + r'''
from datetime import datetime, timezone
run = case / 'run.json'
state = json.loads(run.read_text())
end = int(run.stat().st_mtime)
model = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
since = datetime.fromtimestamp(end - 90, timezone.utc).isoformat()
until = datetime.fromtimestamp(end + 30, timezone.utc).isoformat()
events = subprocess.run(['docker', 'events', '--since', since, '--until', until, '--filter', f'image={model}',
                         '--filter', 'type=container', '--format', '{{json .}}'],
                        capture_output=True, check=True, timeout=45)
records = [json.loads(line) for line in events.stdout.splitlines()]
summary = [{'action': item['Action'], 'delta_seconds': item['time'] - end,
            'exit_code': item['Actor']['Attributes'].get('exitCode')}
           for item in records if item.get('Action') in ('oom', 'die', 'kill')]
print(json.dumps(dict(status=state.get('status'), returncode=state.get('returncode'), error=state.get('error'),
                      timeout_seconds=state.get('timeout_seconds'), model_events=summary), separators=(',', ':')))
'''
elif options.get('stop_bs1024'):
    assert tokens == 1024 and profile and mode is None and not memory_retry
    source = prefix + r'''
from concurrent.futures import ThreadPoolExecutor

model = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
cases = {
    'Window': 'ht-tunable-workspace-186dfa18-bs1024-fp32-profile',
    'LogicalChunk': 'ht-tunable-workspace-186dfa18-bs1024-fp32-logical-profile',
    'PhysicalChunk': 'ht-tunable-workspace-186dfa18-bs1024-fp32-physical-profile-mem256',
}

def selected_containers():
    active = docker('ps', '--filter', 'ancestor=' + model, '--no-trunc', '-q').stdout.split()
    rows = json.loads(docker('inspect', *[item.decode() for item in active]).stdout) if active else []
    selected = {}
    for label, name in cases.items():
        command = '/work/inter-server-native/' + name + '/adapter/run.py'
        found = [row['Id'] for row in rows if row['Image'] == model and row['State']['Running'] and
                 command in (row['Config']['Cmd'] or []) and any(
                     mount['Destination'] == '/work' and Path(mount['Source']) == MOUNT.parent
                     for mount in row['Mounts'])]
        assert len(found) <= 1, label
        if found:
            selected[label] = found[0]
    return selected

before = selected_containers()
with ThreadPoolExecutor(max_workers=3) as pool:
    requests = {label: pool.submit(subprocess.run, ['docker', 'stop', '--time', '8', container],
                                  capture_output=True, timeout=35) for label, container in before.items()}
    exit_codes = {label: request.result().returncode for label, request in requests.items()}
after = selected_containers()
report = {}
for label, name in cases.items():
    case = MOUNT / name
    record = case / 'run.json'
    state = json.loads(record.read_text()) if record.is_file() else {}
    log = case / 'sim.log'
    result = dict(running_before=label in before, running_after=label in after,
                  docker_stop_exit=exit_codes.get(label), run_status=state.get('status'),
                  log_bytes=log.stat().st_size if log.is_file() else None)
    if case.is_dir():
        write_json(case / 'user-stop.json', result)
    report[label] = result
print(json.dumps(report, separators=(',', ':')))
'''
elif options.get('raise_live_limit'):
    assert tokens == 1024 and profile and mode in (None, 'logical') and not memory_retry
    source = prefix + f'\ncase = MOUNT / {case!r}\n' + r'''
model = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
expected = '/work/inter-server-native/' + case.name + '/adapter/run.py'
current = json.loads((case / 'run.json').read_text())
if current['status'] != 'running':
    result = {'status': current['status'], 'updated': False}
else:
    active = docker('ps', '--filter', 'ancestor=' + model, '--no-trunc', '-q').stdout.split()
    rows = json.loads(docker('inspect', *[item.decode() for item in active]).stdout) if active else []
    matched = [row for row in rows if row['Image'] == model and row['State']['Running'] and
               expected in row['Config']['Cmd'] and any(mount['Destination'] == '/work' and
                  Path(mount['Source']) == MOUNT.parent for mount in row['Mounts'])]
    assert len(matched) <= 1
    if not matched:
        result = {'status': 'case_container_no_longer_running', 'updated': False}
    else:
        row, = matched
        previous = row['HostConfig']['Memory']
        assert previous in (32 * 1024**3, 256 * 1024**3)
        if previous == 32 * 1024**3:
            docker('update', '--memory=256g', '--memory-swap=512g', row['Id'])
        updated = json.loads(docker('inspect', row['Id']).stdout)[0]
        assert updated['HostConfig']['Memory'] == 256 * 1024**3
        result = {'status': 'running', 'updated': previous != updated['HostConfig']['Memory'],
                  'old_memory_gib': previous // 1024**3, 'new_memory_gib': updated['HostConfig']['Memory'] // 1024**3,
                  'running': updated['State']['Running'], 'case': case.name}
        write_json(case / 'container-memory-update.json', result)
print(json.dumps(result, separators=(',', ':')))
'''
elif options.get('suite_status'):
    assert profile
    suffix_memory = '-mem256' if memory_retry else ''
    suite = {label: f'ht-tunable-workspace-186dfa18-bs{tokens}-fp32{suffix}-profile{suffix_memory}'
             for label, suffix in (('Window', ''), ('LogicalChunk', '-logical'), ('PhysicalChunk', '-physical'))}
    source = prefix + f'\nsuite = {suite!r}\nresource_status = {bool(tokens == 1024)!r}\n' + r'''
report = {}
for mode, case_name in suite.items():
    case = MOUNT / case_name
    record = case / 'run.json'
    summary = case / 'unified-result.json'
    entry = {'exists': case.is_dir()}
    for path in (record, summary):
        if path.is_file():
            data = json.loads(path.read_text())
            entry.update({key: data[key] for key in ('status', 'kernel_sha256', 'returncode', 'runner_exit_code',
                         'sim_us', 'sst_wall_seconds', 'runner_wall_seconds', 'guards', 'cqe') if key in data})
    log = case / 'sim.log'
    if log.is_file():
        raw = log.read_bytes()
        stamps = [int(match) for match in re.findall(rb'Sim:\s*(\d+)[^\n]*WrCQEResp', raw)]
        entry.update(observed_log_cqe=raw.count(b'WrCQEResp'), last_cqe_stamp=max(stamps, default=None))
    report[mode] = entry
if resource_status:
    model = 'sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
    active = docker('ps', '--filter', 'ancestor=' + model, '--no-trunc', '-q').stdout.split()
    containers = json.loads(docker('inspect', *[value.decode() for value in active]).stdout) if active else []
    matching = {}
    for mode, case_name in suite.items():
        expected = '/work/inter-server-native/' + case_name + '/adapter/run.py'
        for row in containers:
            if row['State']['Running'] and expected in row['Config']['Cmd'] and any(
                mount['Destination'] == '/work' and Path(mount['Source']) == MOUNT.parent for mount in row['Mounts']
            ):
                matching[row['Id']] = mode
                report[mode]['memory_limit_gib'] = row['HostConfig']['Memory'] // 1024**3
    if matching:
        stats = docker('stats', '--no-stream', '--no-trunc', '--format', '{{json .}}', *matching, timeout=45)
        for item in map(json.loads, stats.stdout.splitlines()):
            mode = matching[item['ID']]
            report[mode]['memory_usage'] = item['MemUsage']
            report[mode]['cpu_percent'] = item['CPUPerc']
print(json.dumps(report, separators=(',', ':')))
'''
elif options.get('status'):
    source = prefix + f'\ncase = MOUNT / {case!r}\n' + r'''
report = {'exists': case.is_dir()}
for name in ('run.json', 'unified-result.json', 'phase-summary.json'):
    path = case / name
    if path.exists():
        data = json.loads(path.read_text())
        report[name] = {key: data[key] for key in ('status', 'returncode', 'runner_exit_code', 'sim_us',
                            'kernel_sha256', 'guards', 'checked', 'exports_sha256', 'sst_wall_seconds',
                            'runner_wall_seconds', 'cqe', 'error') if key in data}
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
    data = (here / ('pipeline-bf16.o.gz' if bf16 else 'pipeline.o.gz')).read_bytes()
    binary_sha = '45982fb6c494379d20429d4801b66a51cd98c65afed80790d0b458744b8f3899'
    if bf16:
        binary_sha = '593de6ace0b6bf04477914c3e20fd40b00d4fdf767716cb8a3c52342fe59a867'
    assert hashlib.sha256(gzip.decompress(data)).hexdigest() == binary_sha
    suffix = '-bf16' if bf16 else f'-{mode}' if mode else ''
    plans = (here / f'plans-t{tokens}{suffix}.zip').read_bytes()
    exports = (support / 'exports.py').read_text()
    cast_fix = None
    if bf16:
        repaired = json.loads((root / 'combine_cast/results/v310-cast-library-fix.json').read_text())
        libraries = repaired['library_source']['result']['libs']
        cast_fix = dict(image=repaired['regression']['result']['image'],
                        libraries={'/opt/sim/plugins/' + name: sha for name, sha in libraries.items()})
        exports = exports.replace('6d4cd3b729177646ca1c43d57971e5266a946405b6f820f3d0d914114b7ccfce',
                                  libraries['libDavinciSimulator.so'])
    fields = ('gather', 'gather_ready', 'partial', 'ready', 'local_ready', 'returned', 'completed', 'output', 'status')
    export_fields = fields + ('profile',) if profile else fields
    exports = exports.replace('("returned", "completed", "status", "output")', repr(export_fields)).replace(
        '{"returned", "completed", "status", "output"}', repr(set(export_fields)))
    if profile:
        exports = exports.replace('def export_actual(', (here / 'profile.py').read_text() + '\n\ndef export_actual(', 1)
        call = '''            payload = reconstruct_die_output(
                paths, local, target["size"], decoder_library
            )'''
        assert exports.count(call) == 1
        exports = exports.replace(call, '''            if name == 'profile':
                assert digest(decoder_library) == V310_DECODER_SHA256
                profile_region = dict(addr=local, size=target['size'])
                payload, _, missing = decode_sparse_profile(dump_dir, row['rank'], profile_region, 48)
                assert not missing
            else:
                payload = reconstruct_die_output(paths, local, target['size'], decoder_library)''')
        compile(exports, '<numeric-and-profile-exports>', 'exec')
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
        'timeout=600)', f'timeout={timeout + adapter_margin})')
    if cast_fix:
        phase = phase.replace('sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5',
                              cast_fix['image'])
    if memory_retry:
        assert phase.count('"--memory=32g"') == 1
        phase = phase.replace('"--memory=32g"', '"--memory=256g"')
    modules = {'config': (shared / 'config.py').read_text(), 'route': (shared / 'route.py').read_text(),
               'shared_fixture': (shared / 'fixture.py').read_text(),
               'runtime': (here / 'runtime.py').read_text()}
    fixture = (here / 'fixture.py').read_text()
    fixture = 'import struct\n\n' + fixture[fixture.index('ENTRY = '):]
    green = (shared / 'green.py').read_text().split('\ndef main(', 1)[0].replace('    import runtime\n','')
    green = green.replace('(native,runtime))',
                          "(native,runtime), plans.read(f'{rank}/send.bin') if MODE == 'direct' else None, PROFILE, BF16)")
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
        f'CASE = {case!r}\nENTRY = {entry!r}\nKERNEL_SHA256 = {binary_sha!r}\n'
        f'TOKENS = {tokens}\nHIDDEN = {256 if bf16 else 128}\nCHUNKS = {len(ends)}\nMODE = {mode!r}\n'
        f'PROFILE = {profile!r}\nBF16 = {bf16!r}\nCAST_FIX = {cast_fix!r}\n'
        f'CONTAINER_MEMORY_GIB = {256 if memory_retry else 32}\n',
        green,
        '''
from time import monotonic

def run(request):
    case = MOUNT / CASE
    assert not case.exists()
    support = MOUNT.parent / BUILD / 'source/tools/unitest/simulator/experiments/inter_server_send/sst'
    prepare(request, case, support)
    if CAST_FIX:
        environment = case / 'sst-environment.json'
        record = json.loads(environment.read_text())
        record['decoder_library_sha256'] = CAST_FIX['libraries'][record['decoder_library']]
        record.setdefault('runtime_files', {}).update(CAST_FIX['libraries'])
        write_json(environment, record)
        rehash(case)
    started = monotonic()
    result = execute_phase(case)
    direct = MODE == 'direct'
    result.update(revision=request['revision'], tokens=TOKENS, blocks=BLOCKS, hidden=HIDDEN, chunks=CHUNKS,
                  send_cores=8 if direct else 16, qp_counts=[8, 8] if direct else [16, 16],
                  sync={'logical': 'LogicalChunk', 'physical': 'PhysicalChunk'}.get(MODE, 'Window'),
                  rotation='ServerStaggered' if direct else 'Legacy', reuse_gather=not direct, profiling=PROFILE,
                  container_memory_gib=CONTAINER_MEMORY_GIB)
    if BF16:
        result.update(dtype='bf16', entry=ENTRY, sst_image=CAST_FIX['image'])
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
transport_timeout = 700 if options.get('recover_profile') else timeout + transport_margin
if any(options.get(key) for key in ('status', 'suite_status', 'capacity', 'termination', 'raise_live_limit', 'stop_bs1024', 'cq', 'direct_activity', 'read_profile')):
    transport_timeout = 900 if tokens == 1024 and (options.get('cq') or options.get('read_profile')) else 120
with askpass_environment() as env:
    result = subprocess.run(ssh, input=source.encode(), env=env, capture_output=True,
                            timeout=transport_timeout)
if result.returncode:
    print(json.dumps({'ssh_exit': result.returncode, 'stderr_bytes': len(result.stderr)}))
else:
    print(json.dumps(json.loads(result.stdout), separators=(',', ':')))
