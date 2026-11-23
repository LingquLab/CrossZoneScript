"""Build the cross-group die0 SQ stripe control and run 4 MiB SyncAll."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    revision = args["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        parser.error("invalid candidate revision")
    parallel = args.get("parallel", False)
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
        if not parallel:
            wait_for_recovery()
        config.write_text(json.dumps(dict(
            build=directory, expected_revision=revision, kind="performance",
            size="4mib-per-rank", timeout=3600, deadline=0, schedules=["syncall"],
        )), encoding="utf-8")
        raise SystemExit(subprocess.run([sys.executable, str(scripts / "run_cases.py"),
                                         "--args-file", str(config)]).returncode)


if __name__ == "__main__":
    main()
