"""Validate retained rank data, then restart the revised EP32 matrix."""

import json
import subprocess
import sys
import time
import uuid

from campaign import BASE, CASES, bounded_output, case_name, finalize_case, remote_python, write_status


def reconcile(peer):
    unit = "tilexr-" + BASE.name
    marker = BASE / "abc-reconciled"
    if not marker.exists():
        subprocess.run(["systemctl", "stop", f"{unit}-recovery.timer",
                        f"{unit}-queue.service", f"{unit}-report.service"],
                       check=False, timeout=105)
        write_status(phase="validating_retained")
        reused, repeated = [], []
        for case in CASES:
            name = case_name(case)
            folder = BASE / "runs" / name
            if (folder / "report.json").is_file():
                (folder / "failure.json").unlink(missing_ok=True)
                continue
            while True:
                states = [bounded_output(remote_python(host, "inspect-case", name, str(index)))
                          for index, host in enumerate((None, peer))]
                if all(states) and not any(json.loads(state)["active"] for state in states):
                    break
                time.sleep(5)
            if not any(json.loads(state)["exists"] for state in states):
                continue
            statuses = [
                subprocess.run(remote_python(host, "collect-node", str(index), *map(str, case)),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90).returncode
                for index, host in enumerate((None, peer))
            ]
            if 255 in statuses:
                raise RuntimeError("peer transport interrupted during recovery")
            if statuses == [0, 0]:
                finalize_case(peer, case)
                (folder / "failure.json").unlink(missing_ok=True)
                reused.append(name)
            else:
                attempt = "incomplete-" + uuid.uuid4().hex
                for host in (peer, None):
                    subprocess.run(remote_python(host, "archive-case", name, attempt), timeout=20, check=True)
                repeated.append(name)
        marker.write_text(json.dumps(dict(reused=reused, queued_again=repeated)), encoding="utf-8")
    write_status(phase="reconciled", cases=len(CASES), **json.loads(marker.read_text()))
    subprocess.run(["systemctl", "enable", "--now", f"{unit}-recovery.timer"],
                   timeout=25, check=True)


if __name__ == "__main__":
    reconcile(sys.argv[1])
