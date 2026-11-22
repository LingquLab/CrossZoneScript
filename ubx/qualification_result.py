"""Summarize a finished qualification and close the blocked campaign's report worker."""

import json
from pathlib import Path
import re
import shlex
import sys

from stage_sources import connect, run_remote


REMOTE = r'''
import json
from pathlib import Path
import re
import subprocess
import sys

root = Path(sys.argv[1])
qualification = json.loads((root / "qualification-status.json").read_text(encoding="utf-8"))
if qualification["phase"] != "failed":
    raise ValueError("qualification does not have the expected failed status")
output = Path(qualification["path"])
print(json.dumps(qualification))
by_rank = set()
first_errors = []
for name in ("stderr.log", "stdout.log"):
    data = (output / name).read_text(errors="replace")
    for line in data.splitlines():
        if not re.search(r'error|fail|return err|out of range|invalid|exception|not supported|check result', line, re.I):
            continue
        line = re.sub(r'^\[(\d+)\]\s*', r'\1 ', line)
        rank = re.match(r'^(\d+) ', line)
        if rank:
            by_rank.add(int(rank[1]))
            line = line[rank.end():]
        if line not in first_errors and len(first_errors) < 9:
            first_errors.append(line[:220])
print(json.dumps({"error_rank_count": len(by_rank), "first_errors": first_errors}))
campaign = json.loads((root / "status.json").read_text(encoding="utf-8"))
if campaign["phase"] == "failed":
    snapshot = {"phase": "blocked", "qualification": str(output),
                "reason": "Matching EP32 HCCL AIV qualification failed",
                "previous": campaign,
                "completed_cases": len(list((root / "runs").glob("*/report.json")))}
    (root / "blocked.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    unit = "tilexr-" + root.name + "-report"
    subprocess.run(["systemctl", "stop", unit], capture_output=True, timeout=15, check=False)
    for suffix in ("queue", "report", "qualification"):
        status = subprocess.run(["systemctl", "is-active", "tilexr-" + root.name + "-" + suffix],
                                capture_output=True, timeout=7, text=True)
        print(suffix + "=" + status.stdout.strip())
    print("blocked_record=" + str(root / "blocked.json"))
'''


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace = args["namespace"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
        raise ValueError("invalid namespace")
    with connect(args["host"]) as client, client.open_sftp() as transfer:
        root = transfer.normalize(".") + "/.local/state/tilexr-ubx/" + namespace
        code, output, _ = run_remote(client, "/usr/bin/python3 - " + shlex.quote(root),
                                      timeout=45, input_text=REMOTE)
        if code:
            raise SystemExit(code)
        print(output)
