# Copyright (c) 2026 TileXR Project.
"""Read bounded synthetic-case crash logs and host debugger/core metadata."""

import json
from pathlib import Path
import re
import shutil
import sys

WORK = Path(__file__).resolve().parent


def main(args):
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args["case_id"])
    root = WORK / "cases" / args["case_id"]
    logs = []
    for phase in ("send-only", "send-fault-a1", "send-fault-a2"):
        folder = root / phase
        log = folder / "sim.log"
        entry = dict(phase=phase, log_exists=log.is_file())
        if log.is_file():
            entry["log_bytes"] = log.stat().st_size
            with log.open("rb") as handle:
                handle.seek(max(0, log.stat().st_size - 16384))
                lines = handle.read().decode(errors="replace").splitlines()
            entry["last_lines"] = [line[:350] for line in lines if line.strip()][-8:]
        entry["core_files"] = ([dict(name=f.name, bytes=f.stat().st_size)
                                for f in sorted(folder.glob("core*"))[:4]] if folder.is_dir() else [])
        logs.append(entry)
    return dict(ok=True, diagnostic_only=True, logs=logs,
                host_debuggers={tool: shutil.which(tool) for tool in ("gdb", "lldb", "coredumpctl", "addr2line", "eu-stack")},
                core_pattern=Path("/proc/sys/kernel/core_pattern").read_text().strip())


if __name__ == "__main__":
    try:
        result = main(json.loads(sys.argv[1]))
    except (OSError, ValueError, AssertionError) as error:
        result = dict(ok=False, error_type=type(error).__name__, detail=str(error)[:300])
    print(json.dumps(result, ensure_ascii=True))
