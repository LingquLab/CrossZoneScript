"""Read the A/B/C report service's runtime and failure boundary."""

import json
from pathlib import Path, PurePosixPath
import shlex
import sys

from dispatch_abc_setup import NAMESPACE
from stage_sources import connect, run_remote

PROBE = r"""
set -eu
cd "$1"
unit=tilexr-${PWD##*/}
status=$(systemctl show "$unit-report.service" "$unit-recovery.timer"     -p Id -p ActiveState -p SubState -p Result -p MainPID -p NRestarts -p MemoryPeak)
printf '%s\n' "$status"
journalctl -q -u "$unit-report.service" -u "$unit-recovery.service" -n 48 --no-pager -o cat     | awk '/Traceback|Error:|FileNotFound|status=|elapsed|Failed|exited|Killed|oom/' | cut -c1-190 | tail -14
journalctl -q -k --since '30 minutes ago' --no-pager -o cat     | awk '/Out of memory|oom-kill|Killed process/' | cut -c1-180 | tail -5
if [ -f report-status.json ]; then head -c 2600 report-status.json; printf '\n'; fi
for file in runs/*/tail-analysis.log; do
    if [ -s "$file" ]; then
        printf '%s\n' "$file"
        tail -3 "$file" | cut -c1-180
    fi
done | tail -14
"""

if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with connect(args["host"]) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / NAMESPACE
        status, output, _ = run_remote(client, "bash -s -- " + shlex.quote(str(root)), input_text=PROBE)
        if status:
            raise SystemExit(status)
        print(output)
