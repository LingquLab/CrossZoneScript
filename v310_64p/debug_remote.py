import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

case_name = 'dispatch_memory_64p_top6_profile_bridge_20261119_run08_host_gdb'
case = '/opt/sim/simulator/Testcases/' + case_name
logs = Path('/root/sim-workspace/results') / case_name


def simulator_pid():
    top = subprocess.run(['docker', 'top', 'simbox', '-eo', 'pid,comm'],
                         check=True, capture_output=True, text=True, timeout=5)
    for line in top.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) != 2 or not fields[0].isdigit() or fields[1] not in ('sst', 'sstsim.x'):
            continue
        pid = int(fields[0])
        try:
            if os.readlink(f'/proc/{pid}/cwd') == case:
                return pid
        except FileNotFoundError:
            continue
    return None


def collect_stack(pid, output):
    script = [
        'set debuginfod enabled off',
        'set print frame-arguments none',
        f'set sysroot /proc/{pid}/root',
        f'attach {pid}',
        'handle SIGSEGV stop print pass',
        'continue',
        'info symbol $pc',
        'info registers pc sp x0 x1 x2',
        'info args',
        'bt 24',
        'detach',
    ]
    command = ['gdb', '-q', '-nx', '-batch']
    for instruction in script:
        command.extend(('-ex', instruction))
    with output.open('xb') as stream:
        try:
            return subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                  timeout=1880).returncode
        except subprocess.TimeoutExpired:
            return None


def run():
    logs.mkdir()
    evidence = {'case': case, 'debug_output_directory': str(logs)}
    started = time.monotonic()
    with (logs/'controller.stdout.log').open('wb') as stdout, \
         (logs/'controller.stderr.log').open('wb') as stderr:
        controller = subprocess.Popen(['docker', 'exec', '-i', 'simbox', 'python3', '-B', '-'],
                                      stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
        with controller.stdin:
            controller.stdin.write(run_script)
        pid = None
        for _ in range(175 if 'probe_mode' in globals() else 35):
            pid = simulator_pid()
            if pid is not None or controller.poll() is not None:
                break
            time.sleep(1)
        evidence['host_pid_attached'] = pid
        if pid is not None:
            stack = logs/'gdb.log'
            evidence['gdb_exit_code'] = collect_stack(pid, stack)
            raw = stack.read_bytes()
            evidence['gdb_log_sha256'] = hashlib.sha256(raw).hexdigest()
            lines = raw.decode(errors='replace').splitlines()
            evidence['captured_sigsegv'] = any('received signal SIGSEGV' in line for line in lines)
            evidence['stack_excerpt'] = [
                line[:300] for line in lines if re.search(
                    r'^#\d+|received signal SIGSEGV|^(pc|sp|x0|x1|x2)\s|^this\s*=|^No symbol matches|^ptrace:', line)
            ][:24]
            if not evidence['captured_sigsegv']:
                evidence['gdb_log_tail'] = [line[:200] for line in lines[-8:]]
        try:
            evidence['controller_exit'] = controller.wait(
                timeout=max(10, 2140-(time.monotonic()-started)))
        except subprocess.TimeoutExpired:
            evidence['controller_exit'] = None
            evidence['controller_status'] = 'TIMEOUT_INSPECT_SAVED_STATE'
        if evidence['controller_exit'] == 0:
            evidence['simulation'] = json.loads((logs/'controller.stdout.log').read_text())
    evidence['wall_seconds'] = round(time.monotonic()-started, 3)
    (logs/'host-debug-receipt.json').write_text(json.dumps(evidence, indent=2)+'\n')
    return evidence


print(json.dumps(run(), ensure_ascii=True, separators=(',', ':')))
