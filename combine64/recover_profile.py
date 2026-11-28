"""Recheck a completed SST run using only owned and committed profiling bytes."""
from pathlib import Path
import sys
import time

from sim_case import ROOT, check_outputs, read_json, write_json
from profile_case import summarize

case = ROOT / 'cases' / sys.argv[1]
record_file = case / 'phase-result.json'
before = read_json(record_file)
assert before['returncode'] == 0 and before['status'] == 'failed'
assert 'Missing dump bytes:' in before['error'] and 'profile.bin' in before['error']
started = time.monotonic()
write_json(case / 'before-profile-recovery.json', before)
old = case / 'actual'
if old.is_dir():
    old.rename(case / 'actual-before-profile-recovery')
record = dict(before)
environment = read_json(case / 'sst-environment.json')
check_outputs(case, environment, record)
record.update(summarize(case, 64, record['cores'], record['send_rows']))
record['status'] = 'sst_and_checker_passed'
record['recovery_check_wall_seconds'] = round(time.monotonic() - started, 3)
del record['error']
write_json(record_file, record)
