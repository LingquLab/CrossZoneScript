"""Inspect and restore a pending EP32 read rerun after service interruption."""

import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys

from deploy import deploy
from stage_sources import connect, run_remote


DIAGNOSTIC = r'''
import json
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
prefix = "tilexr-" + root.name
read = json.loads((root / "retry-status.json").read_text())
case = read.get("case") or "stages-read-bs32-off"
units = {}
for suffix in ("queue", "report", "read-retry", "recovery.timer", case):
    result = subprocess.run(["systemctl", "is-active", prefix + "-" + suffix], capture_output=True, text=True)
    units[suffix] = result.stdout.strip()
log = subprocess.run(["journalctl", "-q", "-u", prefix + "-read-retry", "--no-pager", "-n", "6",
                      "-o", "cat"], capture_output=True, text=True)
print(json.dumps({"retry": read, "units": units, "recent": log.stdout.splitlines()[-6:]}))
'''


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    name = args["namespace"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        raise ValueError("invalid namespace")
    host = args["host"]
    with connect(host) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / name
        code, output, _ = run_remote(client, "/usr/bin/python3 - " + shlex.quote(str(root)),
                                      timeout=20, input_text=DIAGNOSTIC)
        if code:
            raise RuntimeError("could not inspect persisted rerun")
        check = json.loads(output)
        print(json.dumps(check), flush=True)
    if (check["units"]["read-retry"] == "inactive" and
            check["retry"]["phase"] == "waiting_for_main_queue"):
        config = {"namespace": name, "files": ["retry_transport.py"],
                  "program": "retry_transport.py", "interpreter": "/usr/bin/python3",
                  "unit": "read-retry", "argv": [host, args["peer"], "stages-read-bs32-off"]}
        print(json.dumps({"restored": deploy(host, config)}))
