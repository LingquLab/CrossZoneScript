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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    revision = args["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        parser.error("invalid candidate revision")
    scripts = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="urma64p-stripe-control-") as temp:
        config = Path(temp) / "args.json"
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
        wait_for_recovery()
        config.write_text(json.dumps(dict(
            build=directory, expected_revision=revision, kind="performance",
            size="4mib-per-rank", timeout=3600, deadline=0, schedules=["syncall"],
        )), encoding="utf-8")
        raise SystemExit(subprocess.run([sys.executable, str(scripts / "run_cases.py"),
                                         "--args-file", str(config)]).returncode)


if __name__ == "__main__":
    main()
