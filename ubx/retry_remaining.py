"""Complete transport-interrupted Dispatch cases using detached node services."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

from campaign import BASE, CASES, bounded_output, case_name, finalize_case, process, remote_python
from retry_transport import read, retry


def node_states(peer, name):
    states = [bounded_output(remote_python(host, "inspect-case", name, str(index)))
              for index, host in enumerate((None, peer))]
    return list(map(json.loads, states)) if all(states) else None


def run_pair(peer, coordinator, case):
    name = case_name(case)
    unit = "tilexr-" + BASE.name + "-" + name
    with socket.socket() as sock:
        sock.bind((coordinator, 0))
        port = sock.getsockname()[1]
    rendezvous = f"{coordinator}:{port}"
    capture = uuid.uuid4().hex

    def launch(index, host):
        command = ["systemd-run", "--quiet", "--collect", "--property=Type=exec",
                   "--property=RuntimeMaxSec=960", "--unit=" + unit, "/usr/bin/python3",
                   str(BASE / "campaign.py"), "run-node", str(index), capture, rendezvous, *map(str, case)]
        return subprocess.run(process(host, command), capture_output=True, timeout=22).returncode

    with ThreadPoolExecutor(max_workers=2) as pool:
        started = [pool.submit(launch, index, host) for index, host in enumerate((None, peer))]
        codes = []
        for task in started:
            try:
                codes.append(task.result())
            except (OSError, subprocess.SubprocessError):
                codes.append(None)
    deadline = time.monotonic() + 980
    while time.monotonic() < deadline:
        states = node_states(peer, name)
        if states:
            if all(state["done"] for state in states) and not any(state["active"] for state in states):
                for attempt in range(4):
                    try:
                        finalize_case(peer, case)
                        return
                    except subprocess.SubprocessError:
                        if attempt == 3:
                            raise
                        time.sleep(5)
            if any(not (state["active"] or state["done"]) for state in states):
                raise RuntimeError(f"node service stopped before producing all rank summaries (launch {codes})")
        time.sleep(6)
    raise RuntimeError("node service completion deadline")


def status(phase, case=None):
    target = BASE / "retry-remaining.json"
    temporary = target.with_suffix(".part")
    temporary.write_text(json.dumps({"phase": phase, "case": case}), encoding="utf-8")
    temporary.replace(target)


def main(coordinator, peer):
    previous = "tilexr-" + BASE.name + "-read-retry"
    status("active")
    while True:
        state = read(BASE / "status.json").get("phase")
        if state == "failed":
            status("failed")
            return
        previous_state = subprocess.run(["systemctl", "is-active", "--quiet", previous], timeout=8)
        if state == "completed" and previous_state.returncode:
            break
        time.sleep(8)
    for case in CASES:
        name = case_name(case)
        failure = read(BASE / "runs" / name / "failure.json")
        if "255" in failure.get("reason", ""):
            status("active", name)
            retry(coordinator, peer, name, pair=run_pair)
    status("completed")


if __name__ == "__main__":
    try:
        main(*sys.argv[1:])
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        status("failed")
        raise
