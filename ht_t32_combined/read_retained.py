"""Recover the two retained T32 run receipts without starting SST."""
import json
from pathlib import Path
import re
import subprocess
import time

remote = r"""
import json
from pathlib import Path
import re
import subprocess

root = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
out = {}
for case, suffix in (('original', ''), ('distributed', '-distributed')):
    folder = root / ('t32-sst-mte-send-5851c89d' + suffix)
    row = {'exists': folder.is_dir()}
    keys = ('status', 'returncode', 'run', 'guards', 'runner_exit_code', 'runner_exception_type',
            'sst_simulated_microseconds', 'exported_rank_count', 'container_stopped',
            'stage_error', 'outputs', 'transport_evidence', 'cqe_log_records')
    for name in ('run.json', 'task-result.json', 't32-summary.json'):
        path = folder / name
        if path.is_file():
            try:
                record = json.loads(path.read_text())
                row[name] = {key: record[key] for key in keys if key in record}
            except ValueError:
                row[name] = {'read_error': 'invalid_json'}
    log = folder / 'sim.log'
    if log.is_file():
        row['log_bytes'] = log.stat().st_size
        with log.open('rb') as stream:
            stream.seek(max(0, row['log_bytes'] - 65536))
            tail = stream.read()
        ends = re.findall(rb'Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)', tail)
        row['completion_marker'] = [part.decode('ascii') for part in ends[-1]] if ends else None
        with log.open('rb') as stream:
            row['cqe_log_records'] = sum(line.count(b'WrCQEResp') for line in stream)
    row['exported_rank_dirs'] = len(list((folder / 'actual').glob('rank*')))
    for name in ('t32-failure.log', 'native-runner.log', 'check.log'):
        path = folder / name
        if not path.is_file():
            continue
        tail = path.read_bytes()[-32768:].decode('utf-8', 'replace')
        errors = re.findall(r'(?m)^([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception|Expired)):', tail)
        calls = re.findall(r'(?m)in (run_t32|main|docker|prepare_bulk|transport_evidence|compare_outputs|run|verify_guards|export_actual)', tail)
        row[name] = {'bytes': path.stat().st_size, 'errors': errors[-5:], 'calls': calls[-8:]}
    try:
        process = subprocess.run(['docker', 'inspect', '--format', '{{.State.Status}} {{.State.OOMKilled}} {{.State.ExitCode}}',
                                  'tilexr-cpu-b4f4660a-ht-t32-send' + suffix], capture_output=True, timeout=8)
        fields = process.stdout.decode('ascii', 'replace').split()
        if process.returncode == 0 and len(fields) == 3:
            row['container_state'] = fields
    except (subprocess.TimeoutExpired, OSError):
        row['container_inspect_unavailable'] = True
    out[case] = row
print(json.dumps(out, separators=(',', ':')))
"""

ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
       "-o", "ConnectTimeout=6", "root@141.61.94.58", "python3", "-"]
attempts = []
for index in range(4):
    try:
        proc = subprocess.run(ssh, input=remote.encode(), capture_output=True, timeout=26)
    except subprocess.TimeoutExpired:
        attempts.append({"type": "transport_timeout"})
    else:
        if proc.returncode == 0:
            print(json.dumps(json.loads(proc.stdout), separators=(",", ":")))
            break
        detail = proc.stderr.decode(errors="replace").lower()
        category = next((name for phrase, name in (
            ("connection timed out", "connect_timeout"), ("connection refused", "connect_refused"),
            ("permission denied", "permission_denied"), ("no route to host", "no_route"),
            ("host key verification failed", "host_key_verification"),
        ) if phrase in detail), "unclassified")
        errors = re.findall(r"(?m)^([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception|Expired)):", proc.stderr.decode(errors="replace"))
        attempts.append(dict(ssh_exit=proc.returncode, type=category, errors=errors[-3:]))
    if index < 3:
        time.sleep(4)
else:
    print(json.dumps({"transport_attempts": attempts}, separators=(",", ":")))
