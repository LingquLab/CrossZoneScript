"""Bound one fixed P2 phase to a private mount view and recorded child identities."""
from pathlib import Path
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from operations import ROOT, CANN, PRIVATE, PRIVATE_ROOT, SYSTEM, integrity, require, save, sha


def identity(pid):
    try:
        fields = Path('/proc/' + str(pid) + '/stat').read_text().rsplit(') ', 1)[1].split()
        return {'pid': pid, 'start_ticks': int(fields[19]), 'state': fields[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def live(record):
    current = identity(record['pid'])
    return current is not None and current['start_ticks'] == record['start_ticks'] and current['state'] != 'Z'


def collect(pid, seen):
    current = identity(pid)
    if current is None:
        return
    key = (pid, current['start_ticks'])
    seen[key] = current
    try:
        children = Path('/proc/' + str(pid) + '/task/' + str(pid) + '/children').read_text().split()
    except (FileNotFoundError, ProcessLookupError):
        children = []
    for child in children:
        child_pid = int(child)
        child_record = identity(child_pid)
        if child_record and (child_pid, child_record['start_ticks']) not in seen:
            collect(child_pid, seen)


def monitor(process, directory, timeout):
    seen = {}
    root = identity(process.pid)
    if root:
        seen[(root['pid'], root['start_ticks'])] = root
    timed_out = False
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            # Follow each still-owned descendant even after its parent exits.
            for record in list(seen.values()):
                if live(record):
                    collect(record['pid'], seen)
            save(directory / 'owned-processes.json', list(seen.values()))
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(.1)
    finally:
        actions = []
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for record in sorted(seen.values(), key=lambda r: r['pid'], reverse=True):
                if not live(record):
                    continue
                actions.append({'pid': record['pid'], 'start_ticks': record['start_ticks'], 'signal': int(sig)})
                try:
                    # Signal only the recorded identity, never an unchecked process group.
                    os.kill(record['pid'], sig)
                except ProcessLookupError:
                    pass
            end = time.monotonic() + 3
            while any(live(r) for r in seen.values()) and time.monotonic() < end:
                process.poll()
                time.sleep(.05)
        residual = [r for r in seen.values() if live(r)]
        save(directory / 'process-monitor.json', {'timed_out': timed_out, 'cleanup_actions': actions, 'own_residual_children': residual})
    return {'namespace_exit': process.wait(timeout=5), 'timed_out': timed_out,
            'cleanup_actions': actions, 'own_residual_children': residual}


def namespace(phase, original_ns):
    require(os.readlink('/proc/self/ns/mnt') != original_ns, 'Private mount namespace missing')
    for target, (source, digest) in PRIVATE.items():
        require(sha(PRIVATE_ROOT / source) == digest, 'Private configuration changed')
        subprocess.run(['mount', '--bind', str(PRIVATE_ROOT / source), target], check=True, capture_output=True, timeout=10)
        subprocess.run(['mount', '-o', 'remount,bind,ro', target], check=True, capture_output=True, timeout=10)
        require(sha(target) == digest, 'Private bind SHA mismatch')
    integrity(private=True)
    os.execv('/usr/bin/python3', ['/usr/bin/python3', str(ROOT / 'controller.py'), phase])


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--namespace':
        namespace(sys.argv[2], sys.argv[3])
    require(len(sys.argv) == 2 and sys.argv[1] in ('correctness', 'capture'), 'Expected phase')
    phase = sys.argv[1]
    directory = ROOT / (phase + '-launch')
    directory.mkdir(exist_ok=False)
    result = {'schema': 'tilelang_targeted_p2_107_parent.v1', 'phase': phase,
              'status': 'FAIL_OR_UNVERIFIED', 'npu_launch': None, 'clock': 'NOT_VERIFIED'}
    try:
        integrity()
        build = json.loads((ROOT / 'build-summary.json').read_text())
        require(build['status'] == 'PASS', 'Build has not passed')
        for name, digest in build['compiled_artifacts'].items():
            require(sha(ROOT / name) == digest, 'Built artifact changed')
        for target, (source, digest) in PRIVATE.items():
            require(sha(PRIVATE_ROOT / source) == digest, 'Private source changed')
        require(not (ROOT / phase).exists(), 'Saved phase exists; audit before retry')
        require(not any(os.environ.get(k) for k in ('LD_PRELOAD', 'ASCEND_VISIBLE_DEVICES',
                    'ASCEND_RT_VISIBLE_DEVICES', 'TILEXR_UDMA_ATTACH_EXISTING_RA')), 'Unexpected inherited overrides')
        original_ns = os.readlink('/proc/self/ns/mnt')
        argv = ['unshare', '--mount', '--propagation', 'private', '/usr/bin/python3',
                str(Path(__file__).resolve()), '--namespace', phase, original_ns]
        prefix = ('source ' + shlex.quote(str(CANN / 'set_env.sh')) + ' >/dev/null 2>&1 && '
                  'export ASCEND_HOME_PATH=' + shlex.quote(str(CANN)) + ' && '
                  'export LD_LIBRARY_PATH=' + shlex.quote(str(ROOT / 'core-install/lib64')) + ':${LD_LIBRARY_PATH:-} && '
                  'export TILEXR_UDMA_ATTACH_EXISTING_RA=1 HCCL_CONNECT_TIMEOUT=120 HCCL_EXEC_TIMEOUT=180 && exec ')
        with (directory / 'namespace.stdout.log').open('xb') as out, (directory / 'namespace.stderr.log').open('xb') as err:
            process = subprocess.Popen(['bash', '-c', prefix + shlex.join(argv)], stdin=subprocess.DEVNULL,
                                       stdout=out, stderr=err, start_new_session=True)
            save(directory / 'launcher.json', {'pid': process.pid, 'identity': identity(process.pid), 'argv': argv})
            result.update(monitor(process, directory, 4000 if phase == 'correctness' else 13300))
        path = ROOT / phase / 'summary.json'
        child = json.loads(path.read_text()) if path.exists() else {}
        result.update(groups=child.get('groups', []), npu_launch=child.get('npu_launch'),
                      worker_status=child.get('status', 'ABSENT'), worker_summary_sha256=sha(path) if path.exists() else None)
        result['original_configuration_preserved'] = (original_ns == os.readlink('/proc/self/ns/mnt')
            and all(sha(p) == digest for p, digest in SYSTEM.items()))
        integrity()
        expected = 12 if phase == 'correctness' else 40
        require(result['namespace_exit'] == 0 and not result['timed_out'] and not result['cleanup_actions']
                and not result['own_residual_children'] and result['original_configuration_preserved'], 'Phase lifecycle failed')
        require(child.get('status') == 'PASS' and len(result['groups']) == expected, 'Phase acceptance incomplete')
        variants = ('B', 'U', 'R64', 'R128')
        require(child.get('variants') == list(variants), 'Four variant worker binding')
        counts = {v: sum(g.get('variant') == v and g.get('status') == 'PASS' for g in result['groups']) for v in variants}
        require(all(n == (3 if phase == 'correctness' else 10) for n in counts.values()), 'Per-variant group acceptance incomplete')
        result.update(variants=list(variants), variant_group_counts=counts,
                      schedule_plan_sha256=child['schedule_plan_sha256'], schedule_actual_sha256=child['schedule_actual_sha256'])
        result['status'] = 'PASS'
    except Exception as error:
        result.update(error_type=type(error).__name__, error_detail=str(error)[:350])
    save(directory / 'summary.json', result)
    print(json.dumps(result, separators=(',', ':')))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
