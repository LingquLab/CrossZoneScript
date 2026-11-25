# Copyright (c) 2026 TileXR Project.
"""Stop the identified legacy SST controller left behind by an SSH cancellation."""
import argparse
import json
import os
from pathlib import Path
import subprocess

SESSION = 'de038086-2b21-40e2-bad9-65360186ad97'
REMOTE = r'''
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

ROOT = Path('/mnt/share/tilexr-sim-jobs/de038086-2b21-40e2-bad9-65360186ad97/run-86f802f-a1')
CID = json.loads((ROOT / 'identity.json').read_text())['container_id']
REV = '86f802f9f8a19728364c466b0e9cb7416e038595'
info = json.loads(subprocess.check_output(['docker','inspect',CID], timeout=10))[0]
assert info['Id'] == CID and info['Config']['Labels']['crosszone.session'] == 'de038086-2b21-40e2-bad9-65360186ad97'
assert any(m['Source'] == str(ROOT) and m['Destination'] == '/work' for m in info['Mounts'])
assert json.loads((ROOT/'identity.json').read_text())['revision'] == REV

def proc(pid):
    try:
        path = Path('/proc') / str(pid)
        cmd = path.joinpath('cmdline').read_bytes().rstrip(b'\0').decode(errors='replace').split('\0')
        stat = path.joinpath('stat').read_text().rsplit(')', 1)[1].split()
        return cmd, int(stat[1]), int(stat[19])
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None

def docker_config(cmd):
    if len(cmd) != 9 or cmd[:4] != ['docker','exec',CID,'timeout'] or not re.fullmatch(r'/work/tools/[0-9a-f]{16}/sim_case.py',cmd[-2]):
        return None
    try:
        cfg = json.loads(cmd[-1])
    except ValueError:
        return None
    if cfg.get('revision') == REV and cfg.get('tokens') in (32,64,128) and cfg.get('attempt') == 1 and 'profiling' not in cfg:
        return cfg
    return None

def controllers():
    found = []
    for item in Path('/proc').iterdir():
        if not item.name.isdigit():
            continue
        child = proc(item.name)
        cfg = docker_config(child[0]) if child else None
        if not cfg:
            continue
        controller = proc(child[1])
        supervisor = proc(controller[1]) if controller else None
        if controller and supervisor and controller[0][-1:] == ['-'] and supervisor[0] == ['timeout','--kill-after=30','94390','python3','-']:
            found.append((int(item.name), child[1], controller[1], controller[2], supervisor[2], cfg))
    return found

deadline = time.monotonic() + 8
owned = controllers()
while not owned and time.monotonic() < deadline:
    time.sleep(.5)
    owned = controllers()
assert len(owned) <= 1, 'multiple_legacy_controllers'
report = {'legacy_candidates':len(owned),'changed':False}
if owned:
    docker_pid, controller, supervisor, stamp, timeout_stamp, cfg = owned[0]
    assert proc(controller)[2] == stamp and proc(supervisor)[2] == timeout_stamp
    os.kill(controller, signal.SIGSTOP)
    try:
        code = r"""
import json,os,re,signal,time
from pathlib import Path
revision = '86f802f9f8a19728364c466b0e9cb7416e038595'
def task_pids():
    workers=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            argv=p.joinpath('cmdline').read_bytes().rstrip(b'\0').decode(errors='replace').split('\0')
            if len(argv)!=3 or not argv[0].endswith('python3') or not re.fullmatch(r'/work/tools/[0-9a-f]{16}/sim_case.py',argv[1]):
                continue
            c=json.loads(argv[2])
            if c.get('revision')==revision and c.get('attempt')==1 and c.get('tokens') in (32,64,128) and 'profiling' not in c:
                workers.append(int(p.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
    return workers
workers=task_pids()
assert len(workers)<=1
for pid in workers:
    try: os.kill(pid,signal.SIGTERM)
    except ProcessLookupError: pass
deadline=time.monotonic()+28
while task_pids() and time.monotonic()<deadline: time.sleep(.3)
remaining=task_pids()
print(json.dumps({'signalled_workers':workers,'remaining_workers':remaining}))
raise SystemExit(bool(remaining))
"""
        p = subprocess.run(['docker','exec',CID,'python3','-c',code], capture_output=True, timeout=38)
        report['worker_check'] = json.loads(p.stdout) if p.stdout.strip() else {}
        report['container_exit'] = p.returncode
    finally:
        if proc(controller) and proc(controller)[2] == stamp:
            os.kill(controller, signal.SIGTERM)
            os.kill(controller, signal.SIGCONT)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and proc(controller) and proc(controller)[0] != ['']:
        time.sleep(.2)
    controller_gone = proc(controller) is None or proc(controller)[0] == ['']
    report.update(changed=True, interrupted_case=f"t{cfg['tokens']}-{cfg['action']}-a1",
                  controller_gone=controller_gone, old_controller=controller)
print(json.dumps(report,separators=(',',':')))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    assert json.loads(parser.parse_args().args_file.read_text(encoding='utf-8')) == {}
    key = Path(os.environ['USERPROFILE']) / '.ssh/id_ed25519'
    command = ['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
               '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26',
               'timeout --kill-after=5 65 python3 -']
    result = subprocess.run(command,input=REMOTE.encode(),capture_output=True,timeout=85)
    logs = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr' / SESSION / '86f802f'
    logs.mkdir(parents=True,exist_ok=True)
    (logs/'stop-legacy.stderr').write_bytes(result.stderr)
    if result.returncode == 0:
        print(json.dumps(json.loads(result.stdout),separators=(',',':')))
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
