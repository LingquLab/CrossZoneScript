"""Read the bounded status of the send-only SST cases."""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(root/'ht_t32_e2e'), str(root/'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST

options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ['--args-file'] else {}
tokens = sorted({int(value) for value in options.get('tokens', [])})
assert set(tokens) <= {32, 64, 128}
diagnostics = bool(options.get('diagnostics'))
aggregate = bool(options.get('aggregate'))
known = {int(size): {int(seed) for seed in seeds} for size, seeds in options.get('known', {}).items()}
source = f"tokens = {tokens!r}\ndiagnostics = {diagnostics!r}\naggregate = {aggregate!r}\nknown = {known!r}\n" + r'''
import json
from pathlib import Path
import time
import re

root = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
cases = {}
passes = {}
for case in sorted(root.glob('ht-peer-order-v*-t*')):
    if not case.is_dir() or (tokens and not any(f'-t{size}-' in case.name for size in tokens)):
        continue
    result = case/'benchmark-result.json'
    run = case/'run.json'
    log = case/'sim.log'
    if result.is_file():
        fields = json.loads(result.read_text())
        cases[case.name] = {k: fields[k] for k in ('status', 'cqe', 'sim_us', 'runner_exit_code') if k in fields}
        if fields['status'] == 'sst_and_checker_passed':
            _, _, _, _, size, seed, mode = case.name.split('-')
            passes.setdefault((int(size[1:]), int(seed)), {})[mode] = fields
    else:
        fields = dict(status=json.loads(run.read_text())['status'] if run.is_file() else 'preparing')
        if log.is_file():
            fields.update(cqe=log.read_bytes().count(b'WrCQEResp'),
                          silence_seconds=round(time.time() - log.stat().st_mtime))
        cases[case.name] = fields
    if diagnostics and cases[case.name]['status'] == 'timed_out':
        fields = cases[case.name]
        status = json.loads(run.read_text())
        fields['timeout_seconds'] = status.get('timeout_seconds')
        fields['returncode'] = status.get('returncode')
        fields['trace_files'] = len(list(case.glob('OUTPUT/stat_trace/Host*_ba_layer_trace.json')))
        if log.is_file():
            stamps = []
            for line in log.read_bytes().splitlines():
                if b'WrCQEResp' not in line:
                    continue
                found = re.findall(rb'\bSim:\s*(\d+)', line)
                if found:
                    stamps.append(int(found[-1]))
            fields['max_model_cqe_stamp'] = max(stamps, default=None)
            fields['last_model_cqe_stamps'] = stamps[-8:]
if aggregate:
    summary = {size: dict(valid_pairs=0, running=[], timed_out=0, new_pairs={}) for size in tokens}
    for (size, seed), pair in passes.items():
        if set(pair) != {'rotate', 'peer'}:
            continue
        rotate, peer = pair['rotate'], pair['peer']
        for key in ('route_sha256', 'payload_by_lane', 'cqe', 'checked', 'guard_count', 'exports_sha256'):
            assert rotate[key] == peer[key], (size, seed, key)
        summary[size]['valid_pairs'] += 1
        if seed not in known.get(size, set()):
            summary[size]['new_pairs'][seed] = [rotate['sim_us'], peer['sim_us']]
    for name, row in cases.items():
        _, _, _, version, size, seed, mode = name.split('-')
        info = summary[int(size[1:])]
        if row['status'] in ('preparing', 'running'):
            info['running'].append([int(seed), mode, version, row['status'], row.get('cqe')])
        if row['status'] == 'timed_out':
            info['timed_out'] += 1
    cases = summary
print(json.dumps(cases, separators=(',', ':')))
'''
ssh = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '-o',
       'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1', '-o', 'PreferredAuthentications=password',
       '-o', 'PubkeyAuthentication=no', HOST, 'python3', '-']
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=85)
if proc.returncode:
    print(json.dumps(dict(ssh_exit=proc.returncode, stderr_bytes=len(proc.stderr))))
else:
    print(json.dumps(json.loads(proc.stdout), separators=(',', ':')))
