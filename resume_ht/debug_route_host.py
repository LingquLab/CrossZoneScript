# Copyright (c) 2026 TileXR Project.
"""Run one retained synthetic SST case under host GDB and save its crash stack."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import time

OLD = Path('/var/tmp/tilexr-combine-ht-500e2a93-b84e-4d36-a9aa-8d2cd0321cdf-v11')
NAME = 'tilexr-combine-ht-500e2a93-v11'
CID = 'e9ab0e97f6b31670b6ce68412fdf20c15203502b9aa8fc0840d45e927a0254a4'
SHA = '2e31f0e06fe776f0a0feb5232f6c9593e3ca453a33e7a5983176dc8b7b7883fd'
REL = 'resume-125e/gdb-a2'
TAG = 'ht-gdb-125e-a2'


def call(*args, timeout=25):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    assert p.returncode == 0, 'read_command_failed'
    return p.stdout


def processes():
    return [line.split(maxsplit=1) for line in call('docker', 'top', NAME, '-eo', 'pid,args').splitlines()[1:]]


def main():
    assert socket.gethostname().split('.')[0] == 'host-94-26'
    info = json.loads(call('docker', 'inspect', '--format', '{{json .}}', NAME))
    assert info['Id'] == CID and info['State']['Running']
    assert info['Image'] == 'sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f'
    assert info['HostConfig']['NetworkMode'] == 'none'
    assert any(m['Source'] == str(OLD) and m['Destination'] == '/work' for m in info['Mounts'])
    assert not any(re.search(r'(^|/|\s)sst(sim\.x)?(\s|$)', p[1]) for p in processes()), 'existing_sst_is_active'
    assert hashlib.sha256((OLD / 'tilexr_moonep_combine_ht_kernel.o').read_bytes()).hexdigest() == SHA
    baseline = OLD / 'cases/8p-bs4-a1/send-only'
    previous = json.loads((baseline / 'run.json').read_text())
    assert previous['returncode'] == -11 and not previous['timed_out']
    manifest = json.loads((baseline.parent / 'case.json').read_text())
    assert manifest['kernel_sha256'] == SHA and manifest['sst_threads'] == 8
    folder = OLD / REL
    folder.mkdir(parents=True, exist_ok=False)
    for name in ('v310_config', 'UBusSim_config'):
        shutil.copytree(baseline / name, folder / name)
    for name in ('installed_model.py', 'test_UBusSim.py', 'workload.yml', 'environment.snapshot.json'):
        shutil.copyfile(baseline / name, folder / name)
    env = json.loads((folder / 'environment.snapshot.json').read_text())['settings']
    snapshot = json.loads((folder / 'environment.snapshot.json').read_text())
    container_root = Path('/proc') / str(info['State']['Pid']) / 'root'
    for record in snapshot['runtime_files'].values():
        digest = hashlib.sha256()
        with (container_root / record['path'].lstrip('/')).open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
        assert digest.hexdigest() == record['sha256'], 'simulator_environment_changed'
    # This child stops before exec so the host can attach before SST starts.
    command = ['bash', '-c', 'set -eo pipefail; source "$1" >&2; shift; exec "$@"',
               'tilexr-sst', env['sst_env_script'], '/opt/sim/local/sstcore/libexec/sstsim.x',
               '-n', '8', '/work/' + REL + '/test_UBusSim.py']
    child = 'import os,signal; os.kill(os.getpid(),signal.SIGSTOP); os.execvp("bash",' + repr(command) + ')'
    start_utc = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    target = None
    with (folder / 'sim.log').open('wb') as output:
        worker = subprocess.Popen(['docker', 'exec', '-w', '/work/' + REL, NAME, 'python3', '-c', child, TAG],
                                  stdout=output, stderr=subprocess.STDOUT)
        try:
            for _ in range(50):
                matches = [int(p[0]) for p in processes() if TAG in p[1]]
                if matches:
                    assert len(matches) == 1
                    target = matches[0]
                    if Path('/proc/' + str(target) + '/status').read_text().split('State:')[1].lstrip().startswith('T'):
                        break
                assert worker.poll() is None, 'sst_launcher_exited_before_attach'
                time.sleep(0.1)
            else:
                raise AssertionError('stopped_debug_target_not_found')
            sysroot = '/proc/' + str(target) + '/root'
            debugger = ['timeout', '--kill-after=10', '210', '/usr/bin/gdb', '--batch',
                        '-ex', 'set pagination off', '-ex', 'set confirm off',
                        '-ex', 'set sysroot ' + sysroot,
                        '-ex', 'set follow-exec-mode same',
                        '-ex', 'handle SIGSTOP nostop noprint nopass',
                        '-ex', 'attach ' + str(target), '-ex', 'continue',
                        '-ex', 'bt 12', '-ex', 'info registers pc x30 x0 x1 x2',
                        '-ex', 'info symbol *(void**)event',
                        '-ex', 'frame 1', '-ex', 'info registers x19 x20 x21 x22 x23 x24 x25 x26 x27 x28',
                        '-ex', 'x/8i $pc-24',
                        '-ex', 'thread apply all bt 4', '-ex', 'info sharedlibrary', '-ex', 'kill']
            with (folder / 'gdb.log').open('wb') as log:
                debug = subprocess.run(debugger, stdout=log, stderr=subprocess.STDOUT, timeout=230)
        finally:
            if worker.poll() is None and target is not None and Path('/proc/' + str(target)).exists():
                os.kill(target, signal.SIGCONT)
                os.kill(target, signal.SIGTERM)
            try:
                worker.wait(timeout=15)
            except subprocess.TimeoutExpired:
                assert target is not None
                os.kill(target, signal.SIGKILL)
                worker.wait(timeout=15)
    assert not any(TAG in p[1] or re.search(r'(^|/|\s)sstsim\.x(\s|$)', p[1]) for p in processes()), 'debug_process_still_active'
    text = (folder / 'gdb.log').read_text(errors='replace')
    result = dict(ok=True, diagnostic_only=True, kernel_sha256=SHA, report='/work/' + REL,
                  diagnostic_started_utc=start_utc, diagnostic_finished_utc=datetime.now(timezone.utc).isoformat(),
                  diagnostic_wall_s=time.monotonic()-start, gdb_exit_code=debug.returncode,
                  debuggee_exit_code=worker.returncode, no_sst_active=True,
                  fault_captured='SIGSEGV' in text and bool(re.search(r'^#0\s', text, re.M)),
                  backtrace=[line[:900] for line in text.splitlines() if re.match(r'^#\d+\s', line)][:32],
                  register_lines=[line[:250] for line in text.splitlines() if re.match(r'^(pc|x[0-9]+)\s', line)],
                  debugger_messages=[line[:500] for line in text.splitlines()
                                     if any(s in line for s in ('SIGSEGV', 'ptrace:', 'No stack.', 'exited normally', 'Cannot access memory'))][:8],
                  simulator_log_bytes=(folder / 'sim.log').stat().st_size)
    result['caller_instructions'] = [line[:300] for line in text.splitlines() if re.match(r'^\s*(?:=>\s*)?0x[0-9a-f]+\s+<[^>]+>:\s', line)][:8]
    result['event_type'] = [line[:300] for line in text.splitlines() if 'vtable for' in line][:2]
    from collections import deque
    tail, ba = deque(maxlen=3), deque(maxlen=3)
    with (folder / 'sim.log').open(errors='replace') as handle:
        for line in handle:
            if line.strip(): tail.append(line.strip()[:600])
            if 'BaLayer' in line or 'ba_layer' in line: ba.append(line.strip()[:600])
    result['last_sim_lines'] = list(tail)
    result['last_ba_lines'] = list(ba)
    (folder / 'diagnosis.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=True))
    except Exception as error:
        # Failure details remain in the session's new directory for read-only recovery.
        folder = OLD / REL
        result = dict(ok=False, diagnostic_only=True, error_type=type(error).__name__, detail=str(error)[:300])
        if folder.is_dir():
            (folder / 'error.json').write_text(json.dumps(result)+'\n')
        print(json.dumps(result, ensure_ascii=True))
        raise SystemExit(1)
