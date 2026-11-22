"""Verify and package the completed UBX timing reports and selected traces."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import time

import paramiko

from stage_sources import connect, run_remote


PACK = r'''
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(sys.argv[1])
queue = json.loads((root / "status.json").read_text())
retry_file = root / "retry-status.json"
retry = json.loads(retry_file.read_text()) if retry_file.is_file() else None
state = json.loads((root / "report-status.json").read_text())
if (queue["phase"] != "completed" or
        (retry is not None and retry.get("phase") not in {
            "case_passed", "case_failed", "already_passed", "no_transport_failure", "retry_error"
        }) or
        state["reports"] + len(state["failed_cases"]) != state["expected"]):
    print(json.dumps({"ready": False, "phase": queue.get("phase"),
                      "retry": retry, "report_state": state}))
    raise SystemExit(0)

files = [root / "report.md", root / "report-status.json"]
files.extend(file for file in (retry_file, root / "matrix.json") if file.is_file())
summaries = {}
trace_count = 0
for directory in sorted((root / "runs").iterdir()):
    timing = directory / "timing.json"
    if not timing.is_file():
        continue
    report = json.loads(timing.read_text())
    native = report["native_task_us"]["round_slowest_rank"]
    phase_count = len(report.get("stages", []))
    if not all(native.get(key) is not None for key in ("min", "median", "max")):
        raise ValueError("missing native duration")
    if report["samples"] != 640 or not report["native"]:
        raise ValueError("incorrect rank/sample coverage")
    files.append(timing)
    if directory.name.endswith("-on"):
        if not phase_count or len(report["typical_rounds"]) != 4:
            raise ValueError("profiling on lacks stage data or selected rounds")
        for kind, round_id in report["typical_rounds"].items():
            trace = directory / f"{kind}-round-{round_id}.perfetto.json"
            content = json.loads(trace.read_text())
            if content["round"] != round_id or len({event["pid"] for event in content["traceEvents"]}) != 32:
                raise ValueError("incorrect trace rank/round coverage")
            files.append(trace)
            trace_count += 1
    summaries[directory.name] = dict(native_us=native, abi=report["abi"],
                                     backend=report["backend"], stages=phase_count)
if (root / "matrix.json").is_file():
    for name in summaries:
        cap = int(name.split("-max")[1].split("-")[0])
        metadata = json.loads((root / "runs" / name / "timing.json").read_text())
        if metadata["max_tokens"] != cap or metadata["effective_max_tokens"] != cap:
            raise ValueError("packet cap differs from reported configuration")
        summaries[name]["effective_max_tokens"] = cap
        summaries[name]["reused"] = "reused_from" in metadata
if len(summaries) != state["reports"]:
    raise ValueError("report coverage does not match counters")

target = root / "TileXR-UBX-EP32-report.zip"
temporary = target.with_suffix(".part")
with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=5) as bundle:
    for file in files:
        bundle.write(file, file.relative_to(root))
with ZipFile(temporary) as bundle:
    if bundle.testzip():
        raise ValueError("invalid bundle entry")
temporary.replace(target)
digest = hashlib.sha256(target.read_bytes()).hexdigest()
print(json.dumps({"ready": True, "bundle": str(target), "sha256": digest,
                  "size": target.stat().st_size, "trace_count": trace_count,
                  "summaries": summaries, "failed_cases": state["failed_cases"], "retry": retry},
                 separators=(",", ":")))
'''


def fetch(args):
    host = args["host"]
    namespace = args["namespace"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
        raise ValueError("invalid namespace")
    with connect(host) as client, client.open_sftp() as transfer:
        transfer.get_channel().settimeout(45)
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / namespace
        code, output, _ = run_remote(client, "/usr/bin/python3 - " + shlex.quote(str(root)),
                                      timeout=75, input_text=PACK)
        if code:
            raise RuntimeError("reports or traces failed verification")
        summary = json.loads(output)
        if summary["ready"]:
            home = Path(os.environ["USERPROFILE"])
            downloads = home / "Downloads"
            folder = downloads if downloads.is_dir() else home
            target = folder / (f"TileXR-UBX-{namespace}.zip")
            temporary = target.with_suffix(".part")
            transfer.get(summary["bundle"], str(temporary))
            with temporary.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != summary["sha256"]:
                raise RuntimeError("Windows copy differs from the node bundle")
            temporary.replace(target)
            summary["green_windows_bundle"] = str(target)
        return summary


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    deadline = time.monotonic() + args.get("wait_seconds", 0)
    result = None
    while True:
        try:
            result = fetch(args)
        except (OSError, EOFError, paramiko.SSHException):
            if time.monotonic() >= deadline:
                raise
        if result is not None and result.get("ready"):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(24)
    print(json.dumps(result or {"ready": False, "phase": "status_unavailable"},
                     ensure_ascii=False, separators=(",", ":")))
