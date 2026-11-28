"""Summarize only the test-owned matrix work directories and their log counts."""
import json
from pathlib import Path

root = Path.home() / "tilexr-validation"
cases = []
for path in sorted(root.glob("ht-0b9c3322-ep8-*"))[-6:]:
    marker = path / "matrix.json"
    groups = sorted(p for p in path.iterdir() if p.is_dir() and p.name.startswith(
        ("qp-", "gather-", "final-", "send-", "direct-", "fullmesh-")))
    if groups or marker.is_file() or (path / "qp_probe.log").exists():
        case = {"dir": path.name, "done": marker.is_file(), "groups": len(groups)}
        if marker.is_file():
            result = json.loads(marker.read_text())
            case["build_errors"] = result.get("build_errors")
            case["failure"] = result.get("failure")
            case["status_counts"] = {name: {s: sum(row.get("status") == s for row in rows.values())
                                             for s in ("pass", "failed", "timeout", "devices_busy")}
                                     for name, rows in result.items()
                                     if name in ("qp", "gather", "final", "native_send", "local_return")}
        if groups:
            newest = max(groups, key=lambda p: p.stat().st_mtime)
            case["last"] = newest.name
            case["log_count"] = len(list(newest.glob("rank-*.log")))
        cases.append(case)
print("TILEXR_RESULT " + json.dumps({"cases": cases}, separators=(",", ":")))
