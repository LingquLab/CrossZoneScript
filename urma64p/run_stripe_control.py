"""Build a Full AllToAll two-group candidate and run SST cases."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock


def wait_for_recovery():
    base = Path.cwd() / "runs/urma64p-4mib-recover/1/execution"
    receipt = base / "execution.json"
    expires = time.monotonic() + 16000
    print(json.dumps({"stage": "waiting_for_4mib_recovery"}), flush=True)
    while time.monotonic() < expires:
        if receipt.is_file():
            state = json.loads(receipt.read_text(encoding="utf-8"))
            if state.get("status") == "EXITED":
                break
            if state.get("status") in {"TIMED_OUT", "CANCELLED", "TERMINATION_UNCONFIRMED"}:
                raise RuntimeError("previous recovery did not confirm clean exit")
        time.sleep(30)
    else:
        raise TimeoutError("previous recovery is still active")

    directories = set()
    released = set()
    matrix_released = False
    for line in (base / "stdout.log").read_text(encoding="utf-8").splitlines():
        if not line.startswith("{"):
            continue
        row = json.loads(line)
        if row.get("stage") == "campaign_start":
            directories.add(row["run_dir"])
        if row.get("stage") == "cleanup" and row.get("exit") == 0:
            released.add(row["run_dir"])
        if row.get("stage") == "16mib_batch_released":
            matrix_released = row["released"]
    if not matrix_released or directories - released:
        raise RuntimeError("previous recovery did not release its SST container")


def previous_build(revision):
    base = Path.cwd() / "runs/urma64p-4mib-stripe-control/1/execution"
    receipt = json.loads((base / "execution.json").read_text(encoding="utf-8"))
    if receipt["status"] != "EXITED" or receipt["exit_code"] == 0:
        raise RuntimeError("previous control did not exit with a recoverable failure")
    rows = [json.loads(line) for line in (base / "stdout.log").read_text(encoding="utf-8").splitlines()
            if line.startswith("{")]
    if any(row.get("stage") == "campaign_start" for row in rows):
        raise RuntimeError("previous control already started an SST case")
    builds = [row for row in rows if row.get("stage") == "compile"
              and row.get("passed") and row.get("revision") == revision]
    return builds[-1] if builds else None


def run_cases(scripts, root, build, revision, cases):
    output_lock = Lock()

    def emit(row):
        with output_lock:
            print(json.dumps(row), flush=True)

    def run_sequence(index, sequence):
        directory = root / str(index)
        directory.mkdir()
        failures = []
        for case in sequence:
            config = directory / "case.json"
            config.write_text(json.dumps(dict(case, build=build, expected_revision=revision)), encoding="utf-8")
            for attempt in (1, 2):
                timed_out = False
                cleanup_succeeded = False
                with (directory / "sst.stderr.log").open("ab") as errors:
                    with subprocess.Popen(
                        [sys.executable, str(scripts / "run_cases.py"), "--args-file", str(config)],
                        cwd=directory, stdout=subprocess.PIPE, stderr=errors, text=True,
                    ) as proc:
                        for line in proc.stdout:
                            row = json.loads(line)
                            if row["stage"] == "sst":
                                timed_out |= row.get("run_status") == "timeout"
                            if row["stage"] == "cleanup":
                                cleanup_succeeded = row["exit"] == 0
                            emit(dict(row, attempt=attempt))
                        status = proc.wait()
                if attempt == 2 or not (status and timed_out and cleanup_succeeded):
                    break
                emit(dict(stage="retry_started", kind=case["kind"], size=case["size"],
                          schedules=case["schedules"], attempt=2))
            if status:
                failures.append(dict(kind=case["kind"], size=case["size"], schedules=case["schedules"], exit=status))
        return failures

    with ThreadPoolExecutor(max_workers=len(cases)) as pool:
        jobs = [pool.submit(run_sequence, index, sequence) for index, sequence in enumerate(cases)]
        failures = [failure for job in jobs for failure in job.result()]
    emit(dict(stage="regression_finished", passed=not failures, failures=failures))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    revision = args["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        parser.error("invalid candidate revision")
    parallel = args.get("parallel", False)
    direct_case = args.get("direct_case")
    case_sequences = args.get("case_sequences")
    if sum(map(bool, (parallel, direct_case, case_sequences))) > 1:
        parser.error("select one case mode")
    if case_sequences:
        if not (1 <= len(case_sequences) <= 3 and all(case_sequences)):
            parser.error("supply one to three nonempty case sequences")
        if any(len(case["schedules"]) != 1 for seq in case_sequences for case in seq):
            parser.error("each case in a sequence requires one schedule for independent retries")
    prior = previous_build(revision) if parallel else None
    scripts = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="urma64p-stripe-control-") as temp:
        config = Path(temp) / "args.json"
        if prior:
            directory = prior["run_dir"]
            print(json.dumps(dict(prior, stage="reuse_compile")), flush=True)
        else:
            config.write_text(json.dumps({"revision": revision}), encoding="utf-8")
            build = subprocess.run([sys.executable, str(scripts / "build.py"), "--args-file", str(config)],
                                   capture_output=True, text=True, timeout=700)
            directory = None
            for line in build.stdout.splitlines():
                row = json.loads(line)
                if row.get("stage") == "compile" and row.get("passed") and row.get("revision") == revision:
                    directory = row["run_dir"]
                print(json.dumps(row), flush=True)
            if build.returncode or not directory:
                raise SystemExit(1)
        if case_sequences:
            run_cases(scripts, Path(temp), directory, revision, case_sequences)
            return
        if not parallel and not direct_case:
            wait_for_recovery()
        case = direct_case or dict(size="4mib-per-rank", timeout=3600, deadline=0, schedules=["syncall"])
        config.write_text(json.dumps(dict(
            case, build=directory, expected_revision=revision, kind="performance",
        )), encoding="utf-8")
        raise SystemExit(subprocess.run([sys.executable, str(scripts / "run_cases.py"),
                                         "--args-file", str(config)]).returncode)


if __name__ == "__main__":
    main()
