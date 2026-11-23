# Copyright (c) 2026 TileXR Project.
"""Re-run the failed send phase in a fresh directory and identify the faulting ELF."""

import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import mkdtemp
import time

import sst

BASELINE = Path(json.loads((sst.WORK / "progress.json").read_text())["case"]) / "send-only"
KERNEL_SHA = "c95c61586a5f9e97c83eb862e83b3cfde3ac9a22dec0b6a3332c67434b7ac85e"


def location(address, mappings, report):
    for start, end, file_offset, path in mappings:
        if not start <= address < end or not path.startswith("/"):
            continue
        offset = address - start + file_offset
        entry = dict(elf=Path(path).name, file_offset=hex(offset))
        resolver = shutil.which("addr2line")
        if resolver:
            symbol = subprocess.run([resolver, "-f", "-C", "-e", path, hex(offset)],
                                    capture_output=True, text=True, timeout=10)
            (report / f"symbol-{offset:x}.txt").write_text(symbol.stdout)
            if not symbol.returncode and symbol.stdout:
                name = symbol.stdout.splitlines()[0]
                if name != "??" and re.fullmatch(r"[A-Za-z0-9_ :.*~<>,()+&\[\]-]{1,240}", name):
                    entry["symbol"] = name
        return entry
    return dict(mapped=False, address=hex(address))


def main():
    assert BASELINE.parent.parent == sst.WORK / "cases"
    baseline = json.loads((BASELINE / "run.json").read_text())
    assert baseline["returncode"] == -11
    assert sst.checksum(sst.KERNEL) == KERNEL_SHA
    snapshot = json.loads((BASELINE / "environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)

    case = Path(mkdtemp(prefix="8p-bs4-send-fault-", dir=BASELINE.parent))
    for path in BASELINE.iterdir():
        if path.name in ("v310_config", "UBusSim_config"):
            shutil.copytree(path, case / path.name)
        elif path.name in ("installed_model.py", "test_UBusSim.py", "workload.yml", "environment.snapshot.json"):
            shutil.copyfile(path, case / path.name)
    library = case / "sst_fault.so"
    source = Path(__file__).with_name("sst_fault.c")
    compiler = shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        raise RuntimeError("native compiler unavailable")
    with (case / "build.log").open("wb") as log:
        subprocess.run([compiler, "-Wall", "-Wextra", "-O2", "-shared", "-fPIC",
                        str(source), "-o", str(library)], stdout=log,
                       stderr=subprocess.STDOUT, timeout=30, check=True)
    env = snapshot["settings"]
    fault_path = case / "fault-addresses.txt"
    cmd = sst.shell_command(env, "sst", ["env", "TILEXR_SST_FAULT_FILE=" + str(fault_path),
                           "LD_PRELOAD=" + str(library), env["sst_bin"], "-n", "16",
                           case / "test_UBusSim.py"])
    with (case / "sim.log").open("wb") as log:
        start = time.monotonic()
        try:
            process = subprocess.run(cmd, cwd=case, stdout=log, stderr=subprocess.STDOUT, timeout=420)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            returncode = 124
    result = dict(report=str(case), returncode=returncode, wall_s=round(time.monotonic() - start, 3))
    result["fault_file_bytes"] = fault_path.stat().st_size if fault_path.exists() else None
    result["sst_launcher"] = env["sst_bin"]
    if fault_path.is_file():
        values = fault_path.read_text(errors="replace").splitlines()
        if len(values) >= 3:
            pc, link, fault_address = (int(value, 16) for value in values[:3])
            mappings = []
            for line in values[3:]:
                fields = line.split(maxsplit=5)
                if len(fields) == 6:
                    begin, end = (int(value, 16) for value in fields[0].split("-", 1))
                    mappings.append((begin, end, int(fields[2], 16), fields[5]))
            result.update(pc=location(pc, mappings, case), link=location(link, mappings, case),
                          fault_address=hex(fault_address))
    result["baseline_report"] = str(BASELINE)
    result["kernel_sha256"] = sst.checksum(sst.KERNEL)
    sst.write_json(case / "diagnosis.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
