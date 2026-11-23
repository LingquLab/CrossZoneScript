# Copyright (c) 2026 TileXR Project.
"""Build a null-link name recorder and run the failed input in a new directory.

The recorder stops before the native null dereference. Its run is diagnostic
evidence, not a baseline correctness or performance measurement.
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
    case = Path(json.loads((sst.WORK / "progress.json").read_text())["case"])
    assert case.parent == sst.WORK / "cases"
    baseline = case / "send-only"
    snapshot = json.loads((baseline / "environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    library = Path(snapshot["settings"]["library_dir"]) / "libUBusSimulator.so"
    assert sst.checksum(library) == "f25a51ad798e02b9d1f52f4b9c113ca1467f52a4796c8e6d6afd66b307c14e82"
    report = Path(mkdtemp(prefix="watch-link-", dir=case))
    for path in baseline.iterdir():
        if path.name in ("v310_config", "UBusSim_config"):
            shutil.copytree(path, report / path.name)
        elif path.name in ("installed_model.py", "test_UBusSim.py", "workload.yml", "environment.snapshot.json"):
            shutil.copyfile(path, report / path.name)
    recorder = report / "link_watch.so"
    command = ["c++", "-std=c++17", "-shared", "-fPIC", "-O2", "-I/opt/sim/local/sstcore/include",
               str(sst.WORK / "link_watch.cpp"), "-ldl", "-o", str(recorder)]
    with (report / "build.log").open("wb") as log:
        build = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=45)
    result = dict(report=str(report), build_returncode=build.returncode)
    if build.returncode:
        result["build_errors"] = [line[:400] for line in (report / "build.log").read_text(errors="replace").splitlines() if "error:" in line][:3]
    else:
        env = snapshot["settings"]
        capture = report / "null-component.txt"
        command = sst.shell_command(env, "sst", ["env", "TILEXR_NULL_LINK_REPORT=" + str(capture),
                        "LD_PRELOAD=" + str(recorder), "/opt/sim/local/sstcore/libexec/sstsim.x", "-n", "16", report / "test_UBusSim.py"])
        start = time.monotonic()
        with (report / "sim.log").open("wb") as log:
            try:
                run = subprocess.run(command, cwd=report, stdout=log, stderr=subprocess.STDOUT, timeout=420)
                result["returncode"] = run.returncode
            except subprocess.TimeoutExpired:
                result["returncode"] = 124
        result["wall_s"] = round(time.monotonic() - start, 3)
        if capture.exists():
            name = capture.read_text().strip()
            if not re.fullmatch(r"Host\d+_Udie\d+_Body\d+_ba_layer", name):
                raise ValueError("unexpected component name format")
            result["null_link_component"] = name
            result["active_v310_hosts"] = list(range(8))
    sst.write_json(report / "diagnosis.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
