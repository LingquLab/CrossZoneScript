"""Read the pinned SST case without starting or stopping any workload."""
import json
import subprocess

from green_auth import askpass_environment

remote = r"""
import json
from pathlib import Path
import re
import subprocess
import time

root = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
case = root/'t32-combine-fp32-c4297a51'
result = {'case_exists': case.is_dir(), 'authenticated': True}
keys = ('status', 'kernel_sha256', 'returncode', 'guards', 'run', 'executed_sst', 'sst_simulated_microseconds',
        'checked', 'stage_error', 'first_mismatch', 'runner_exit_code', 'cleanup_error', 'private_container_removed')
for name in ('run.json', 'task-result.json', 'e2e-summary.json'):
    record = case/name
    if record.is_file():
        try:
            data = json.loads(record.read_text())
        except ValueError:
            continue
        result[name] = {key:data[key] for key in keys if key in data}
log = case/'sim.log'
if log.is_file():
    stat = log.stat()
    result['log_bytes'] = stat.st_size
    result['log_silence_seconds'] = max(0, int(time.time()-stat.st_mtime))
    with log.open('rb') as stream:
        stream.seek(max(0, stat.st_size - 65536))
        tail = stream.read()
    matches = re.findall(rb'Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)',tail)
    if matches:
        result['completion_marker'] = [field.decode() for field in matches[-1]]
probe = subprocess.run(['docker','info','--format','{{.ServerVersion}}'],capture_output=True,timeout=8)
result['docker_ready'] = probe.returncode == 0
container = subprocess.run(['docker','inspect','--format','{{.State.Status}} {{.State.OOMKilled}}',
                            'tilexr-cpu-b4f4660a-ht-t32-e2e-c4297a51'],capture_output=True,timeout=8)
if container.returncode == 0:
    status, oom = container.stdout.decode().split()
    result['container'] = {'status': status, 'oom_killed': oom == 'true'}
print(json.dumps(result,separators=(',',':')))
"""
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
