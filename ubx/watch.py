"""Wait for a benchmark transition using the coordinator's persisted queue status."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex
import sys
import time

import paramiko

from status import snapshots
from stage_sources import connect, run_remote


def read_state(client, root):
    command = r'''
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])

def read(path):
    if not path.is_file():
        return None
    return json.loads(path.read_text())

state = read(root / "status.json") or {}
reports = read(root / "report-status.json") or {}
retry = read(root / "retry-status.json") or {}
base = root / "build.exit"
profile = root.with_name(root.name + "-profile") / "build.exit"
print(json.dumps({
    "phase": state.get("phase"),
    "retry": retry.get("phase"), "case": state.get("case"),
    "finished": len(list((root / "runs").glob("*/report.json"))),
    "reported": reports.get("reports", 0),
    "failed_cases": len(list((root / "runs").glob("*/failure.json"))),
    "build": [int(file.read_text()) if file.is_file() else None for file in (base, profile)],
}))
'''
    invocation = "/usr/bin/python3 - " + shlex.quote(root)
    code, output, _ = run_remote(client, invocation, timeout=12, input_text=command)
    if code != 0:
        raise RuntimeError("cannot read coordinator status")
    return json.loads(output)


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace, hosts = args["namespace"], args["hosts"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
        raise ValueError("invalid namespace")
    deadline = time.monotonic() + args.get("wait_seconds", 900)
    baseline = args.get("baseline")
    target = args.get("target_cases")
    state = None
    terminal_retries = {"case_passed", "case_failed", "already_passed", "no_transport_failure",
                        "retry_error", "main_queue_failed", None}
    while time.monotonic() < deadline:
        try:
            with connect(hosts[0]) as client, client.open_sftp() as transfer:
                transfer.get_channel().settimeout(12)
                root = transfer.normalize(".") + "/.local/state/tilexr-ubx/" + namespace
                state = read_state(client, root)
        except (OSError, EOFError, ValueError, RuntimeError, paramiko.SSHException):
            time.sleep(20)
            continue
        if baseline is None:
            baseline = state
        done = state["finished"] + state["failed_cases"]
        ready = state["reported"] + state["failed_cases"]
        if args.get("wait_report"):
            advanced = done == ready == 22 and state["retry"] in terminal_retries
        elif target is not None:
            advanced = done >= target
        else:
            advanced = state != baseline
        if advanced or state["phase"] == "failed" or any(code not in (0, None) for code in state["build"]):
            break
        time.sleep(24)
    print(json.dumps(state if state is not None else {"phase": "status_unavailable"}))
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        futures = [pool.submit(snapshots, host, namespace) for host in hosts]
        for host, future in zip(hosts, futures):
            try:
                future.result()
            except (OSError, EOFError, RuntimeError, paramiko.SSHException) as error:
                print(f"{host} status_unavailable {type(error).__name__}")


if __name__ == "__main__":
    main()
