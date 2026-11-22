"""Retry a single test interrupted before valid rank data could be collected."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from campaign import BASE, CASES, bounded_output, case_name, finalize_case, inspect_case, occupancy, remote_python, run_pair


def status(phase, **details):
    temporary = BASE / "retry-status.part"
    temporary.write_text(json.dumps(dict(phase=phase, **details)), encoding="utf-8")
    temporary.replace(BASE / "retry-status.json")


def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def retry(coordinator, peer, name, pair=run_pair, transport_only=True, allow_incomplete=False):
    case = next(item for item in CASES if case_name(item) == name)
    directory = BASE / "runs" / name
    status("waiting_for_main_queue", case=name)
    while read(BASE / "status.json").get("phase") not in ("completed", "failed"):
        time.sleep(10)
    if read(BASE / "status.json").get("phase") != "completed":
        status("main_queue_failed", case=name)
        return
    if (directory / "report.json").is_file():
        status("already_passed", case=name)
        return
    failure = read(directory / "failure.json")
    if (not failure and not allow_incomplete) or (transport_only and "255" not in failure.get("reason", "")):
        status("no_transport_failure", case=name)
        return
    while True:
        remote = bounded_output(remote_python(peer, "inspect-case", name, "1"))
        if remote:
            local_case, peer_case = inspect_case(name, 0), json.loads(remote)
            if not local_case["active"] and not peer_case["active"]:
                break
        time.sleep(5)
    if local_case["done"] and peer_case["done"]:
        finalize_case(peer, case)
        (directory / "failure.json").unlink(missing_ok=True)
        status("case_passed", case=name, recovered=True)
        start_report(peer)
        return
    attempt = "transport-" + uuid.uuid4().hex
    for host in (peer, None):
        subprocess.run(remote_python(host, "archive-case", name, attempt),
                       timeout=15, check=True)
    ready = str(BASE / "ready" / (case[0] + "-" + case[3]))
    while True:
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(bounded_output, remote_python(peer, "probe", ready))
            local = occupancy()
            remote = future.result()
        if Path(ready).is_file() and local == "I" * 16 and remote == "1 " + "I" * 16:
            break
        status("waiting_for_32p", case=name, coordinator=local, peer=remote, archive=attempt)
        time.sleep(5)
    status("running", case=name, archive=attempt)
    try:
        pair(peer, coordinator, case)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        directory.mkdir(exist_ok=True)
        (directory / "failure.json").write_text(json.dumps({"case": name, "reason": str(error)}),
                                                encoding="utf-8")
        status("case_failed", case=name, archive=attempt, reason=str(error))
    else:
        status("case_passed", case=name, archive=attempt)
    start_report(peer)


def start_report(peer):
    unit = "tilexr-" + BASE.name + "-report"
    running = subprocess.run(["systemctl", "is-active", "--quiet", unit], timeout=8)
    if running.returncode:
        script = "tail_report.py" if (BASE / "tail_report.py").is_file() else "report.py"
        subprocess.run(["systemd-run", "--quiet", "--collect", "--unit=" + unit,
                        "--property=Type=exec", "/usr/bin/python3", str(BASE / script), peer],
                       timeout=15, check=True)


if __name__ == "__main__":
    try:
        retry(*sys.argv[1:])
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        status("retry_error", reason=str(error))
        raise
