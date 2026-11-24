"""Run the consolidated URMA kernels on server 30 in independent SST containers."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
assert set(args) == {"revision"}
revision = args["revision"]
assert re.fullmatch("[0-9a-f]{40}", revision)
scripts = Path(__file__).resolve().parents[1]
reports = Path.cwd() / "urma-consolidation"
reports.mkdir()


def run(label, entry, options, timeout):
    work = reports / label
    work.mkdir()
    request = work / "args.json"
    request.write_text(json.dumps(dict(revision=revision, **options)), encoding="utf-8")
    try:
        with (work / "stdout.log").open("wb") as stdout, (work / "stderr.log").open("wb") as stderr:
            result = subprocess.run([sys.executable, str(scripts / entry), "--args-file", str(request)],
                                    cwd=work, stdout=stdout, stderr=stderr, timeout=timeout)
        status = result.returncode
    except subprocess.TimeoutExpired:
        status = 124
    evidence = []
    for line in (work / "stdout.log").read_bytes().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        evidence.append(row)
    print(json.dumps(dict(label=label, revision=revision, status=status, evidence=evidence)), flush=True)
    return status


def clos():
    statuses = []
    for variant in ("clos-4plus8", "clos-8plus8"):
        statuses.append(run(variant, "urma64p/build_run_clos_4plus8.py",
                            dict(variant=variant, size="512kib-per-rank", modes=["functional"], timeout=1300),
                            2500))
    return any(statuses)


with ThreadPoolExecutor(max_workers=3) as pool:
    jobs = [
        pool.submit(run, "lr8", "lr8/run.py", dict(label="final-"+revision[:8]+"-lr8",
                    cases=[[32, 128]], schedules=[0, 0xffffffff, 35863], profiling=True,
                    routing="with-clos", threads=16, batch_timeout=5000), 5100),
        pool.submit(run, "full-64p", "urma64p/build_run_groups.py", {}, 5900),
        pool.submit(clos),
    ]
    statuses = [job.result() for job in jobs]
raise SystemExit(int(any(statuses)))
