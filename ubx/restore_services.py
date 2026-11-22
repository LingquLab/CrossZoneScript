"""Check why the direct campaign services stopped and resume their saved progress."""

import json
from pathlib import Path
import re
import shlex
import sys

from resume_direct import main as resume
from stage_sources import connect, run_remote


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace = args["namespace"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
        raise ValueError("invalid namespace")
    with connect(args["hosts"][0]) as client:
        unit = "tilexr-" + namespace
        probe = r'''
printf 'boot='; uptime -s
for suffix in queue report; do
    unit=$1-$suffix
    systemctl show "$unit" -p ActiveState -p SubState -p Result -p RuntimeMaxUSec 2>/dev/null || true
    journalctl -q -u "$unit" --no-pager -n 14 -o short-iso 2>/dev/null |       grep -Ei 'stop|failed|runtime|error|status=|signal|traceback' | cut -c1-240 | tail -8 || true
done
'''
        _, out, _ = run_remote(client, "bash -s -- " + shlex.quote(unit), input_text=probe)
        print(out, flush=True)
    resume()
