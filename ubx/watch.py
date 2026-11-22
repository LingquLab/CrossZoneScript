"""Wait for a benchmark transition using the coordinator's persisted queue status."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex
import sys
import time

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
base = root / "build.exit"
profile = root.with_name(root.name + "-profile") / "build.exit"
print(json.dumps({
    "phase": state.get("phase"), "case": state.get("case"),
    "finished": len(list((root / "runs").glob("*/report.json"))),
    "reported": reports.get("reports", 0),
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
    with connect(hosts[0]) as client:
        with client.open_sftp() as transfer:
            root = transfer.normalize(".") + "/.local/state/tilexr-ubx/" + namespace
        baseline = args.get("baseline")
        while True:
            state = read_state(client, root)
            if baseline is None:
                baseline = state
            if (state != baseline or state["phase"] in ("completed", "failed")
                    or any(code not in (0, None) for code in state["build"])):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(15)
        print(json.dumps(state))
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        futures = [pool.submit(snapshots, host, namespace) for host in hosts]
        for future in futures:
            future.result()


if __name__ == "__main__":
    main()
