"""Bounded read-only evidence for a failed or interrupted eight-case batch."""
import json
from pathlib import Path
import re

root, revision, labels = Path(ROOT_VALUE), REV_VALUE, LABELS_VALUE
assert json.loads((root/'build-summary.json').read_text())['runtime_commit'] == revision
suite_root = Path('/opt/sim/simulator/Testcases')
active_sst, owned = [], []
groups = {}
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        stat = (entry/'stat').read_text().rsplit(')', 1)[1].split()
        if stat[0] == 'Z':
            continue
        comm = (entry/'comm').read_text().strip()
        pid = int(entry.name)
        groups.setdefault(int(stat[2]), []).append(pid)
        if comm in ('sst', 'sstsim.x'):
            active_sst.append(dict(pid=pid, comm=comm))
        argv = (entry/'cmdline').read_bytes().split(b'\0')
        # Match only an argv element; the diagnostic source itself is stdin.
        if any(arg == str(root).encode() or arg.startswith(str(root/'case-runner-').encode()) for arg in argv):
            owned.append(dict(pid=pid, comm=comm))
    except (OSError, ValueError, IndexError):
        continue

read_errors = []

def read_json(path):
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except (OSError, ValueError) as error:
        read_errors.append(dict(path=str(path), error_class=type(error).__name__))
        return None

def safe_error(value):
    if value is None:
        return None
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', str(value))
    value = re.sub(r'(?i)(token|password|authorization|secret)\s*[:=]\s*\S+', r'\1=[redacted]', value)
    return value[:240]

rows = []
for label in labels:
    output = root/'cases'/label
    suite = suite_root/('schedule_'+revision[:8]+'_'+label)
    summary, report = read_json(output/'summary.json'), read_json(output/'result.json')
    receipt = read_json(suite/'process_receipt.json')
    row = dict(label=label, output_exists=output.exists(), suite_exists=suite.exists(),
               summary_exists=summary is not None, report_exists=report is not None)
    if summary:
        phase = summary.get('schedule_profile') or {}
        row.update(payload_valid=summary.get('validated'), simulated_us=summary.get('simulated_us'),
                   mode=summary.get('mode'), scheduling_misses=phase.get('scheduling_misses'),
                   scheduling_errors=phase.get('scheduling_errors'))
    if report:
        row.update(report_status=report.get('status'), failed_phase=report.get('failed_phase'),
                   error=safe_error(report.get('error')), elapsed_seconds=report.get('elapsed_seconds'),
                   saved_phases=sorted(report.get('phases', {})))
    if receipt:
        row.update(receipt_returncode=receipt.get('returncode'),
                   receipt_remaining_processes=receipt.get('remaining_processes'),
                   current_receipt_group_pids=groups.get(receipt.get('pid'), []))
    logfile = suite/'run.log'
    if logfile.exists():
        with logfile.open('rb') as stream:
            stream.seek(max(0, logfile.stat().st_size-131072))
            tail = stream.read(131072).decode('utf-8', errors='replace')
        row['log_bytes'] = logfile.stat().st_size
        row['log_tail_signals'] = {name: len(re.findall(pattern, tail, re.I)) for name, pattern in (
            ('complete', r'Simulation is complete'), ('fatal', r'\bfatal\b'),
            ('timeout', r'timed?\s*out|timeout'), ('assert', r'assert(?:ion)?'),
            ('out_of_memory', r'out of memory|bad_alloc|cannot allocate memory'),
            ('segfault', r'segmentation fault|sigsegv'))}
    rows.append(row)
print(json.dumps(dict(read_only=True, simulation_launched=False, runtime_commit=revision,
                      active_sst_count=len(active_sst), active_sst=active_sst[:8],
                      owned_runner_count=len(owned), owned_runners=owned[:8],
                      read_errors=read_errors, records=rows)))
