"""Summarize only the test-owned matrix work directories and their log counts."""
import json
import re
import subprocess
from pathlib import Path

root = Path.home() / "tilexr-validation"
cases = []
for path in sorted(root.glob("ht-0b9c3322-ep8-*"))[-6:]:
    marker = path / "matrix.json"
    groups = sorted(p for p in path.iterdir() if p.is_dir() and p.name.startswith(
        ("qp-", "gather-", "final-", "send-", "direct-", "fullmesh-", "concurrent-", "serial-")))
    if groups or marker.is_file() or (path / "qp_probe.log").exists() or (path / "ep8_concurrent.log").exists():
        case = {"dir": path.name, "done": marker.is_file(), "groups": len(groups)}
        if marker.is_file():
            result = json.loads(marker.read_text())
            case["build_errors"] = result.get("build_errors")
            case["failure"] = result.get("failure")
            if "concurrent" in result:
                case["concurrent_failures"] = {
                    name: [item["rank_pass"], item.get("first_failure")]
                    for name, item in result["concurrent"].items() if item["status"] == "failed"
                }
            case["status_counts"] = {name: {s: sum(row.get("status") == s for row in rows.values())
                                             for s in ("pass", "failed", "timeout", "devices_busy")}
                                     for name, rows in result.items()
                                     if name in ("qp", "gather", "final", "native_send", "local_return", "concurrent")}
        if groups:
            newest = max(groups, key=lambda p: p.stat().st_mtime)
            case["last"] = newest.name
            case["log_count"] = len(list(newest.glob("rank-*.log")))
        cases.append(case)
probe = subprocess.run(["npu-smi", "info"], capture_output=True, text=True, timeout=15)
idle = sorted(set(map(int, re.findall(r"No running processes found in NPU\s+(\d+)", probe.stdout))))
rows = probe.stdout.split("Process id", 1)[-1].splitlines()
busy = sorted(set(int(m.group(1)) for line in rows if
                  (m := re.match(r"^\s*\|\s*(\d+)(?:\s+\d+)?\s*\|\s*\d+\s*\|", line))))
own = subprocess.run(["pgrep", "-f", "tilexr-ep8-matrix|ep8_concurrent|ep8_modules"],
                     capture_output=True, text=True, timeout=5)
processes = []
for pid in own.stdout.split():
    try:
        args = (Path("/proc") / pid / "cmdline").read_bytes().split(b"\0")
    except OSError:
        continue
    executable = Path(args[0].decode(errors="replace")).name if args else ""
    if executable in ("ep8_concurrent", "ep8_modules") or (
            executable.startswith("python") and any(a.endswith(b"/matrix.py") for a in args)):
        processes.append(executable)
print("TILEXR_RESULT " + json.dumps({"cases": cases, "npu": {"exit": probe.returncode, "idle": idle, "busy": busy},
                                      "own_test_processes": processes}, separators=(",", ":")))
