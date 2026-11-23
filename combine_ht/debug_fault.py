# Copyright (c) 2026 TileXR Project.
"""Read the failed SST evidence and capture a bounded native backtrace with GDB.

The session container owns inputs and reports; diagnostic output goes to a new
directory while the original model configuration and kernel remain unchanged.
"""

import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import mkdtemp
import time

import sst


def main():
    progress = json.loads((sst.WORK / "progress.json").read_text())
    baseline = Path(progress["case"]) / "send-only"
    assert baseline.parent.parent == sst.WORK / "cases"
    assert json.loads((baseline / "run.json").read_text())["returncode"] == -11
    snapshot = json.loads((baseline / "environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    report = Path(mkdtemp(prefix="native-debug-", dir=baseline.parent))
    lines = (baseline / "sim.log").read_text(errors="replace").splitlines()
    errors = [line[:700] for line in lines if re.search(r"fatal|error|exception|segmentation|assert|signal", line, re.I)]
    result = dict(report=str(report), baseline=str(baseline), log_bytes=(baseline / "sim.log").stat().st_size,
                  errors=errors[-8:], tail=[line[:400] for line in lines[-6:]],
                  debuggers={name: shutil.which(name) for name in ("gdb", "gdb-multiarch", "lldb")},
                  core_pattern=Path("/proc/sys/kernel/core_pattern").read_text().strip())
    debugger = result["debuggers"]["gdb"] or result["debuggers"]["gdb-multiarch"]
    if debugger:
        for path in baseline.iterdir():
            if path.name in ("v310_config", "UBusSim_config"):
                shutil.copytree(path, report / path.name)
            elif path.name in ("installed_model.py", "test_UBusSim.py", "workload.yml", "environment.snapshot.json"):
                shutil.copyfile(path, report / path.name)
        env = snapshot["settings"]
        argv = [debugger, "-q", "--batch", "-ex", "set pagination off", "-ex", "set print frame-arguments none",
                "-ex", "handle SIGSEGV stop print nopass", "-ex", "run", "-ex", "bt 14",
                "-ex", "info registers pc x30", "-ex", "x/6i $pc-8", "--args", env["sst_bin"], "-n", "16", report / "test_UBusSim.py"]
        started = time.monotonic()
        with (report / "gdb.log").open("wb") as log:
            try:
                process = subprocess.run(sst.shell_command(env, "sst", argv), cwd=report, stdout=log,
                                         stderr=subprocess.STDOUT, timeout=420)
                result["debugger_exit"] = process.returncode
            except subprocess.TimeoutExpired:
                result["debugger_exit"] = 124
        result["wall_s"] = round(time.monotonic() - started, 3)
        debug_lines = (report / "gdb.log").read_text(errors="replace").splitlines()
        selected = [line[:900] for line in debug_lines if re.match(r"^#\d+ |^pc\s|^x30\s|^=>|^\s*0x[0-9a-f]+|.*received signal SIG", line)]
        result["backtrace"] = selected[-26:]
    sst.write_json(report / "diagnosis.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
