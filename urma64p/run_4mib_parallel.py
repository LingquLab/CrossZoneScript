"""Run independent 4 MiB cases in separate SST containers."""

import argparse
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
runner = Path(__file__).resolve().with_name("run_cases.py")
std_lock = Lock()


def launch(root, schedule, build, deadline, revision=None):
    work = root / schedule
    work.mkdir()
    config = work / "input.json"
    case = dict(build=build, kind="performance", size="4mib-per-rank",
                timeout=3600, deadline=deadline, schedules=[schedule])
    if revision:
        case["expected_revision"] = revision
    config.write_text(json.dumps(case), encoding="utf-8")
    with (work / "runner.stderr.log").open("w", encoding="utf-8") as errors:
        with subprocess.Popen([sys.executable, str(runner), "--args-file", str(config)],
                              cwd=work, stdout=subprocess.PIPE, stderr=errors, text=True,
                              encoding="utf-8") as proc:
            for line in proc.stdout:
                row = json.loads(line)
                with std_lock:
                    print(json.dumps(dict(row, parallel_job=schedule)), flush=True)
            return proc.wait()


jobs = [("nosync", args["build"], 0), ("clock", args["build"], args["bootstrap_deadline"])]
if "stripe_control_build" in args:
    jobs.append(("syncall", args["stripe_control_build"], 0, args["stripe_control_revision"]))

with tempfile.TemporaryDirectory(prefix="urma64p-4mib-parallel-") as temp:
    root = Path(temp)
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        pending = [pool.submit(launch, root, *job) for job in jobs]
        outcomes = {job[0]: future.result() for job, future in zip(jobs, pending)}
print(json.dumps({"stage": "parallel_finished", "outcomes": outcomes}), flush=True)
raise SystemExit(int(any(outcomes.values())))
