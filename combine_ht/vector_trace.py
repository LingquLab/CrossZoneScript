# Copyright (c) 2026 TileXR Project.
"""Trace chip zero of the existing SIMD candidate without rebuilding it.

Copies the failed workload to a new report, enabling only instruction dumps.
Full traces remain local; stdout identifies the first fatal and trace files.
"""

from contextlib import redirect_stdout
import json
from pathlib import Path
import re
import subprocess
from tempfile import mkdtemp
import time

import sst


def main():
    case = Path(json.loads((sst.WORK / "progress.json").read_text())["case"])
    assert case.parent == sst.WORK / "cases"
    baseline = case / "gm-state-trial-bskf6_n5/send-only"
    snapshot = json.loads((baseline / "environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    workload = sst.yaml.safe_load((baseline / "workload.yml").read_text())
    report = Path(mkdtemp(prefix="vector-trace-", dir=case))
    configure = sst.configure

    def traced(sources, args):
        args.instruction_dump_chips = [0]
        return configure(sources, args)

    sst.configure = traced
    with (report / "model.log").open("w") as log, redirect_stdout(log):
        sst.write_model(report, snapshot, workload, 8)
    sst.configure = configure
    env = snapshot["settings"]
    start = time.monotonic()
    with (report / "sim.log").open("wb") as log:
        try:
            process = subprocess.run(sst.shell_command(env, "sst", [env["sst_bin"], "-n", "16", report / "test_UBusSim.py"]),
                                     cwd=report, stdout=log, stderr=subprocess.STDOUT, timeout=420)
            code = process.returncode
        except subprocess.TimeoutExpired:
            code = 124
    log_lines = (report / "sim.log").read_text(errors="replace").splitlines()
    result = dict(report=str(report), diagnostic_only=True, returncode=code, wall_s=round(time.monotonic()-start, 3),
                  kernel_sha256=sst.checksum(Path(workload["workload_config"]["chip0"]["kernel_file"])),
                  first_fatal=[line[:800] for line in log_lines if "FATAL:" in line][:1])
    files = [path for path in report.rglob("*") if path.is_file() and path.name not in ("sim.log", "model.log")
             and re.search(r"instr|dump|trace", str(path.relative_to(report)), re.I)]
    files.sort(key=lambda path: (0 if re.search(r"aicore.?2.*aiv.?1|aiv.?1.*aicore.?2", str(path)) else 1, -path.stat().st_size))
    result["trace_file_count"] = len(files)
    result["trace_files"] = [dict(path=str(path), bytes=path.stat().st_size) for path in files[:10]]
    sst.write_json(report / "analysis.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
