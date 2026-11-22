"""Retry the two failed host-barrier runs when the earlier recovery is done."""

import sys
import json
import subprocess
import time

from campaign import BASE
from retry_remaining import run_pair
from retry_transport import retry


def status(phase, case=None):
    path = BASE / "retry-host-barrier.json"
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps({"phase": phase, "case": case}), encoding="utf-8")
    temporary.replace(path)


def main(coordinator, peer):
    previous = "tilexr-" + BASE.name + "-retry-remaining"
    status("active")
    while subprocess.run(["systemctl", "is-active", "--quiet", previous], timeout=8).returncode == 0:
        time.sleep(10)
    for case in ("tail-reviewed-dispatch-bs128-on", "tail-reviewed-dispatch-bs64-off"):
        status("active", case)
        retry(coordinator, peer, case, pair=run_pair, transport_only=False)
    status("completed")


if __name__ == "__main__":
    try:
        main(*sys.argv[1:])
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        status("failed")
        raise
