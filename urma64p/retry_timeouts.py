"""Watch existing SST runs and retry each confirmed timeout in a fresh container."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
cases = args["cases"]
if not cases or len({case["size"] + "/" + case["schedule"] for case in cases}) != len(cases):
    parser.error("supply distinct original cases")
for case in cases:
    if not re.fullmatch(r"/opt/urma64p-cases\.[a-z0-9_]{8}", case["run_dir"]):
        parser.error("invalid original case directory")
    if case["schedule"] not in ("syncall", "clock", "nosync"):
        parser.error("invalid schedule")

output_lock = Lock()
directory = Path(__file__).resolve().parent
ssh = [
    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
    "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
    "root@141.61.94.30", "python3 -u -",
]
remote = """import json,subprocess,time
from pathlib import Path
remaining = {case['run_dir']: case for case in cases}
stop = time.monotonic() + 7200
while remaining and time.monotonic() < stop:
    names = subprocess.check_output(['docker','ps','--format','{{.Names}}'], text=True, timeout=15).splitlines()
    for path, case in list(remaining.items()):
        original = Path(path) / case['schedule']
        candidate = original / 'candidate-result.json'
        reports = sorted(original.glob('runs/*/run.json'))
        if not reports or not candidate.is_file() or Path(path).name.replace('.','-') in names:
            continue
        result = json.loads(candidate.read_text())
        report = json.loads(reports[-1].read_text())
        scenario = json.loads((original/'scenario.json').read_text())
        assert result['schedule'] == case['schedule'] and result['size'] == case['size']
        assert scenario['candidate_revision'] == case['revision']
        print(json.dumps(dict(case, stage='original_finished', status=report['status'],
                   exit=result['exit'], simulated_us=result.get('simulated_us'),
                   deadline=result['deadline'], timeout=report['timeout_seconds'])), flush=True)
        del remaining[path]
    if remaining: time.sleep(30)
if remaining:
    print(json.dumps({'stage':'monitor_deadline','cases':list(remaining)}),flush=True)
    raise SystemExit(1)
"""


def emit(row):
    with output_lock:
        print(json.dumps(row), flush=True)


def retry(root, original):
    label = original["size"] + "-" + original["schedule"]
    work = root / label
    work.mkdir()
    config = work / "case.json"
    config.write_text(json.dumps(dict(
        build=original["build"], expected_revision=original["revision"],
        kind="performance", size=original["size"], timeout=original["timeout"],
        deadline=original["deadline"], schedules=[original["schedule"]],
    )), encoding="utf-8")
    emit(dict(original, stage="retry_started", retry_case=label))
    with (work / "retry.stderr.log").open("wb") as errors:
        with subprocess.Popen([sys.executable, str(directory / "run_cases.py"), "--args-file", str(config)],
                              cwd=work, stdout=subprocess.PIPE, stderr=errors) as proc:
            for line in proc.stdout:
                emit(dict(json.loads(line), retry_case=label, original_run=original["run_dir"]))
            return proc.wait()


with tempfile.TemporaryDirectory(prefix="urma64p-timeout-retry-") as temp:
    root = Path(temp)
    retries = []
    with (root / "monitor.stderr.log").open("wb") as errors:
        with ThreadPoolExecutor(max_workers=len(cases)) as pool:
            with subprocess.Popen(ssh, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors) as proc:
                proc.stdin.write(("cases = " + repr(cases) + "\n" + remote).encode())
                proc.stdin.close()
                for line in proc.stdout:
                    event = json.loads(line)
                    emit(event)
                    if event.get("stage") == "original_finished" and event["status"] == "timeout":
                        retries.append(pool.submit(retry, root, event))
                status = proc.wait()
            outcomes = [job.result() for job in retries]
emit({"stage":"timeout_retry_finished","monitor_exit":status,"retry_exit_codes":outcomes})
raise SystemExit(int(status != 0 or any(outcomes)))
