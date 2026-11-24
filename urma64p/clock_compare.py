"""Compare the old and consolidated 64P Clock object on the same functional fixture."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
request = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
assert set(request) == {"baseline", "candidate", "candidate_build", "deadlines"}
assert all(re.fullmatch("[0-9a-f]{40}", request[k]) for k in ("baseline", "candidate"))
assert re.fullmatch(r"/opt/urma64p-full\.[A-Za-z0-9]{6}", request["candidate_build"])
assert request["deadlines"] and all(type(value) is int and value > 0 for value in request["deadlines"])
scripts = Path(__file__).resolve().parent


def measure(label, revision, deadline, build_dir=None):
    directory = Path.cwd() / ("clock-compare-" + label)
    directory.mkdir()
    args = directory / "args.json"
    if build_dir is None:
        args.write_text(json.dumps(dict(revision=revision)), encoding="utf-8")
        result = subprocess.run([sys.executable, str(scripts / "build.py"), "--args-file", str(args)],
                                cwd=directory, capture_output=True, text=True, timeout=640)
        (directory / "build.stdout").write_text(result.stdout, encoding="utf-8")
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        compiled = [row["run_dir"] for row in rows if row.get("stage") == "compile" and
                    row.get("passed") and row.get("revision") == revision]
        if result.returncode or len(compiled) != 1:
            return dict(label=label, revision=revision, build_exit=result.returncode, evidence=rows)
        build_dir = compiled[0]
    args.write_text(json.dumps(dict(build=build_dir, expected_revision=revision,
                                     kind="functional", size="512kib-per-rank", timeout=950,
                                     deadline=deadline, schedules=["clock"])), encoding="utf-8")
    result = subprocess.run([sys.executable, str(scripts / "run_cases.py"), "--args-file", str(args)],
                            cwd=directory, capture_output=True, text=True, timeout=1200)
    (directory / "run.stdout").write_text(result.stdout, encoding="utf-8")
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    return dict(label=label, revision=revision, deadline=deadline, build_dir=build_dir,
                simulator_command_exit=result.returncode, evidence=rows)


with ThreadPoolExecutor(max_workers=3) as pool:
    jobs = [pool.submit(measure, "baseline", request["baseline"], request["deadlines"][0])]
    jobs.extend(pool.submit(measure, str(deadline), request["candidate"], deadline,
                            request["candidate_build"]) for deadline in request["deadlines"])
    results = [job.result() for job in jobs]
for row in results:
    print(json.dumps(row), flush=True)
assert all("simulator_command_exit" in row and any(item.get("stage") == "sst" for item in row["evidence"])
           for row in results), "comparison did not collect SST results"
