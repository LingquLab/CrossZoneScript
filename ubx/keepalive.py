"""Restart only the two task-owned services when the EP32 campaign is interrupted."""

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def read(name):
    try:
        return json.loads((ROOT / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def unit(suffix):
    return "tilexr-" + ROOT.name + "-" + suffix


def ensure(suffix, script, args):
    name = unit(suffix)
    is_active = subprocess.run(["systemctl", "is-active", "--quiet", name], timeout=8)
    if is_active.returncode != 0:
        subprocess.run(["systemd-run", "--quiet", "--collect", "--unit=" + name,
                        "--property=Type=exec", "/usr/bin/python3", str(ROOT / script), *args],
                       timeout=15, check=True)


if __name__ == "__main__":
    coordinator, peer = sys.argv[1:]
    status = read("status.json")
    report = read("report-status.json")
    pending = status.get("phase") != "completed"
    accounted = report.get("reports", 0) + len(report.get("failed_cases", {}))
    if not pending and accounted == status["cases"]:
        subprocess.run(["systemctl", "disable", "--now", unit("recovery") + ".timer"],
                       timeout=15, check=True)
    else:
        if pending:
            ensure("queue", "campaign.py", ["campaign", coordinator, peer])
        if pending or not report.get("pending_report_errors"):
            ensure("report", "report.py", [peer])
