"""Read the pinned SST case without starting or stopping any workload."""
import json
from pathlib import Path
import subprocess
import sys

from green_auth import askpass_environment

remote = r"""
import json
from pathlib import Path
import re
import subprocess
import time

root = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
case = root/f't32-combine-fp32-{digest}'
result = {'case_exists': case.is_dir(), 'authenticated': True}
keys = ('status', 'kernel_sha256', 'returncode', 'guards', 'run', 'executed_sst', 'sst_simulated_microseconds',
        'checked', 'stage_error', 'first_mismatch', 'runner_exit_code', 'runner_wall_seconds', 'checker_error',
        'checker_log_present', 'exported_rank_count', 'cleanup_error', 'private_container_removed', 'cqe_log_records',
        'timeout_seconds')
result['executed_sst'] = None
for name in ('run.json', 'task-result.json', 'e2e-summary.json'):
    record = case/name
    if record.is_file():
        try:
            data = json.loads(record.read_text())
        except ValueError:
            continue
        result[name] = {key:data[key] for key in keys if key in data}
        if name == 'run.json':
            result['executed_sst'] = 'pid' in data
log = case/'sim.log'
if log.is_file():
    stat = log.stat()
    result['log_bytes'] = stat.st_size
    result['log_silence_seconds'] = max(0, int(time.time()-stat.st_mtime))
    with log.open('rb') as stream:
        result['cqe_log_records'] = 0
        for line in stream:
            result['cqe_log_records'] += line.count(b'WrCQEResp')
            match = re.search(rb'Simulation is complete, simulated time: ([0-9.]+) (ps|ns|us|ms|s)', line)
            if match:
                result['completion_marker'] = [field.decode() for field in match.groups()]
check = case/'check.log'
if check.is_file():
    with check.open('rb') as stream:
        stream.seek(max(0, check.stat().st_size - 65536))
        tail = stream.read().decode(errors='replace').strip()
    try:
        counters = json.loads(tail)
        result['checker'] = {key: counters[key] for key in
                             ('ranks', 'returned_values', 'completion_slots', 'output_values', 'statuses')}
    except (ValueError, KeyError, TypeError):
        mismatch = re.findall(r'(?m)^ValueError: (rank\d\d (?:returned|completed|output)\[\d+\] mismatch: [0-9a-f]+ != [0-9a-f]+)$', tail)
        if mismatch:
            result['first_mismatch'] = mismatch[-1]
        elif 'AssertionError' in tail:
            result['checker_error'] = 'AssertionError'
probe = subprocess.run(['docker','info','--format','{{.ServerVersion}}'],capture_output=True,timeout=8)
result['docker_ready'] = probe.returncode == 0
container = subprocess.run(['docker','inspect','--format','{{.State.Status}} {{.State.OOMKilled}}',
                            f'tilexr-cpu-b4f4660a-ht-t32-e2e-{digest}'],capture_output=True,timeout=8)
if container.returncode == 0:
    status, oom = container.stdout.decode().split()
    result['container'] = {'status': status, 'oom_killed': oom == 'true'}
print(json.dumps(result,separators=(',',':')))
"""
args = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
digest = "a7286b7a" if args.get("sq_stripe") is True else "c4297a51"
remote = f"digest = {digest!r}\n" + remote
compile(remote, "<read_sst_case>", "exec")
command = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8",
           "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1", "-o", "PreferredAuthentications=password",
           "-o", "PubkeyAuthentication=no", "root@141.61.94.58", "python3", "-"]
try:
    with askpass_environment() as environment:
        proc = subprocess.run(command, input=remote.encode(), env=environment, capture_output=True, timeout=35)
    if proc.returncode == 0:
        outcome = json.loads(proc.stdout)
    else:
        error = proc.stderr.decode(errors="replace").lower()
        reason = next((kind for text,kind in (
            ("permission denied", "authentication_denied"), ("connection timed out", "connect_timeout"),
            ("connection refused", "connect_refused"), ("host key verification failed", "host_key_verification"),
            ("ssh_askpass", "askpass_failed"),
        ) if text in error), "unclassified")
        outcome = {"authenticated": False, "reason": reason, "ssh_exit": proc.returncode}
except FileNotFoundError:
    outcome = {"authenticated": False, "reason": "configured_local_auth_note_not_found"}
except subprocess.TimeoutExpired:
    outcome = {"authenticated": False, "reason": "command_timeout"}
except ValueError:
    outcome = {"authenticated": False, "reason": "invalid_receipt"}
print(json.dumps(outcome,separators=(",",":")))
