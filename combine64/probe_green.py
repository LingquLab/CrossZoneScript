# Copyright (c) 2026 TileXR Project.
"""Return bounded activity and completion evidence for the active SST case."""
import argparse
import json
import os
from pathlib import Path
import subprocess

SESSION = 'de038086-2b21-40e2-bad9-65360186ad97'
REVISIONS = {
    '86f802f': '86f802f9f8a19728364c466b0e9cb7416e038595',
    'cb63658': 'cb63658b507aa7eba023afefd9e7ef8a27a0dcde',
}
REMOTE = r'''
import json
from pathlib import Path
import os
import re
import subprocess
import time

root = Path('/mnt/share/tilexr-sim-jobs/de038086-2b21-40e2-bad9-65360186ad97') / f'run-{revision[:7]}-a1'
identity = json.loads((root / 'identity.json').read_text())
assert identity['revision'] == revision
cid = identity['container_id']
info = json.loads(subprocess.check_output(['docker', 'inspect', cid], timeout=10))[0]
assert info['Id'] == cid and info['Config']['Labels']['crosszone.session'] == 'de038086-2b21-40e2-bad9-65360186ad97'
allowed = ('stage', 'status', 'pid', 'started_unix', 'sst_wall_seconds', 'platform_simulated_us',
           'returncode', 'checked_ranks', 'guards', 'error')

def directory_activity(directory):
    samples = []
    for path in directory.rglob('*'):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        if path.is_file():
            samples.append(stat)
    return dict(files=len(samples), bytes=sum(stat.st_size for stat in samples),
                last_write_unix=max((stat.st_mtime for stat in samples), default=None))

def latest():
    progress = json.loads((root / 'progress.json').read_text())
    raw = progress['case']
    assert re.fullmatch(r't(?:4|16|32|64|128)-[a-z-]+-a[0-9]+', raw), 'invalid case name'
    case = root / 'cases' / raw
    report = dict(progress=progress)
    path = case / 'run.json'
    if path.is_file():
        run = json.loads(path.read_text())
        report.update({key: run[key] for key in allowed if key in run})
        if run.get('status') == 'running' and type(run.get('pid')) is int:
            probe = subprocess.run(['docker', 'exec', cid, 'cat', f"/proc/{run['pid']}/stat"],
                                   capture_output=True, timeout=10)
            if probe.returncode == 0:
                fields = probe.stdout.decode().rsplit(')', 1)[1].split()
                report['process_state'] = fields[0]
                report['cpu_ticks'] = int(fields[11]) + int(fields[12])
    log = case / 'sim.log'
    if log.is_file():
        stat = log.stat()
        length = stat.st_size
        with log.open('rb') as f:
            f.seek(max(0, length - 524288), os.SEEK_SET)
            tail = f.read().decode(errors='replace')
        ticks = re.findall(r'Sim: ([0-9]+)[(]cycle[)]', tail)
        completed = re.findall(r'Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)', tail)
        cq_ticks = re.findall(r'Sim: ([0-9]+)[(]cycle[)][^\n]*WrCQEResp', tail)
        report.update(log_bytes=length, log_last_write_unix=stat.st_mtime,
                      last_sim_cycle=int(ticks[-1]) if ticks else None,
                      last_cq_cycle=int(cq_ticks[-1]) if cq_ticks else None,
                      completion_marker=completed[-1] if completed else None)
        report['error_classes'] = [word for word in ('fatal','segmentation fault','assertion','panic')
                                   if word in tail.lower()]
    report['stat_trace'] = directory_activity(case / 'stat_trace')
    report['dumps'] = directory_activity(case / 'bin')
    return report

first = latest()
if first.get('status') == 'running':
    time.sleep(12)
second = latest()
print(json.dumps(dict(first=first, second=second), separators=(',',':')))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    assert set(args) <= {'candidate'}
    candidate = args.get('candidate', '86f802f')
    script = 'revision = ' + repr(REVISIONS[candidate]) + '\n' + REMOTE
    key = Path(os.environ['USERPROFILE']) / '.ssh/id_ed25519'
    cmd = ['ssh', '-T', '-i', str(key), '-p', '22', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
           '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=12',
           'root@141.61.94.26', 'timeout --kill-after=5 55 python3 -']
    process = subprocess.run(cmd, input=script.encode(), capture_output=True, timeout=75)
    logs = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr' / SESSION / candidate
    logs.mkdir(parents=True, exist_ok=True)
    (logs / 'probe.stderr').write_bytes(process.stderr)
    if process.returncode != 0:
        return 1
    print(json.dumps(json.loads(process.stdout), separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
