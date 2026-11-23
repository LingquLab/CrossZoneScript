# Copyright (c) 2026 TileXR Project.
"""Inspect a failed send and capture its SIGSEGV in a separate diagnostic run."""

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

import sst


def location(address, mappings):
    for start, end, file_offset, path in mappings:
        if start <= address < end and path.startswith("/"):
            offset = address - start + file_offset
            row = dict(elf=Path(path).name, file_offset=hex(offset))
            resolver = shutil.which("addr2line")
            if resolver:
                p = subprocess.run([resolver, "-f", "-C", "-e", path, hex(offset)],
                                   capture_output=True, text=True, timeout=10)
                name = p.stdout.splitlines()[0] if p.stdout else ""
                if not p.returncode and name != "??" and re.fullmatch(r"[A-Za-z0-9_ :.*~<>,()+&\[\]-]{1,300}", name):
                    row["symbol"] = name
            return row
    return dict(mapped=False, address=hex(address))


def main(args):
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args["case_id"])
    parent = sst.WORK / "cases" / args["case_id"]
    baseline = parent / "send-only"
    previous = json.loads((baseline / "run.json").read_text())
    assert previous["returncode"] == -11 and not previous["timed_out"]
    manifest = json.loads((parent / "case.json").read_text())
    assert sst.checksum(sst.KERNEL) == manifest["kernel_sha256"]
    snapshot = json.loads((baseline / "environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    attempt = args["attempt"]
    assert type(attempt) is int and 2 <= attempt <= 9
    case = parent / ("send-fault-a" + str(attempt))
    assert not case.exists(), "diagnostic_already_attempted"
    case.mkdir()
    for path in baseline.iterdir():
        if path.name in ("v310_config", "UBusSim_config"):
            shutil.copytree(path, case / path.name)
        elif path.name in ("installed_model.py", "test_UBusSim.py", "workload.yml", "environment.snapshot.json"):
            shutil.copyfile(path, case / path.name)
    fault = case / "fault-addresses.txt"
    env = snapshot["settings"]
    simulator = sst.ROOT / "local/sstcore/libexec/sstsim.x"
    assert simulator.is_file(), "simulator_binary_unavailable"
    gdb = shutil.which("gdb")
    if gdb:
        argv = [gdb, "--batch", "-ex", "set pagination off", "-ex", "set confirm off",
                "-ex", "set follow-fork-mode child", "-ex", "run", "-ex", "bt 24",
                "--args", simulator, "-n", str(manifest["sst_threads"]), case / "test_UBusSim.py"]
        method = "gdb"
    else:
        library = case / "sst_fault.so"
        compiler = shutil.which("cc") or shutil.which("gcc")
        assert compiler, "native_compiler_unavailable"
        with (case / "build.log").open("wb") as log:
            subprocess.run([compiler, "-Wall", "-Wextra", "-O2", "-shared", "-fPIC",
                            str(sst.WORK / "sst_fault.c"), "-o", str(library)], stdout=log,
                           stderr=subprocess.STDOUT, timeout=30, check=True)
        argv = ["env", "TILEXR_SST_FAULT_FILE=" + str(fault), "LD_PRELOAD=" + str(library),
                simulator, "-n", str(manifest["sst_threads"]), case / "test_UBusSim.py"]
        method = "preload_direct_binary"
    command = sst.shell_command(env, "sst", argv)
    start = time.monotonic()
    with (case / "sim.log").open("wb") as log:
        p = subprocess.Popen(command, cwd=case, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = p.wait(timeout=180)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=15)
            code = 124
    result = dict(ok=True, diagnostic_only=True, report=str(case), diagnostic_method=method,
                  diagnostic_process_exit_code=code, fault_captured=False,
                  wall_s=time.monotonic() - start, kernel_sha256=manifest["kernel_sha256"],
                  simulator_sha256=sst.checksum(simulator))
    if fault.is_file() and len(fault.read_text().splitlines()) >= 3:
        lines = fault.read_text().splitlines()
        pc, link, address = [int(line, 16) for line in lines[:3]]
        mappings = []
        for line in lines[3:]:
            fields = line.split(maxsplit=5)
            if len(fields) == 6:
                begin, end = [int(v, 16) for v in fields[0].split("-")]
                mappings.append((begin, end, int(fields[2], 16), fields[5]))
        result.update(pc=location(pc, mappings), link=location(link, mappings), fault_address=hex(address))
        result["fault_captured"] = code == -11
    log_path = case / "sim.log"
    result["log_bytes"] = log_path.stat().st_size
    with log_path.open("rb") as handle:
        handle.seek(max(0, result["log_bytes"] - 32768))
        lines = handle.read().decode(errors="replace").splitlines()
    result["error_categories"] = sorted({name for name in ("FATAL", "ERROR", "SIGSEGV", "Segmentation", "assert")
                                         if any(name in line for line in lines)})
    if gdb:
        result["backtrace"] = [line[:600] for line in lines if re.match(r"^#\d+\s", line)][:24]
        result["fault_captured"] = "SIGSEGV" in result["error_categories"] and bool(result["backtrace"])
    result["simulated_us"] = next((float(match[1]) * sst.TIME_FACTORS[match[2]]
                                    for line in reversed(lines) if (match := sst.SIM_TIME.search(line))), None)
    result["prior_capture_bytes"] = (parent / "send-fault-a1/fault-addresses.txt").stat().st_size
    if not result["fault_captured"]:
        result["next_action"] = "Inspect saved diagnostic log; this result does not establish send success"
    sst.write_json(case / "diagnosis.json", result)
    return result


if __name__ == "__main__":
    try:
        result = main(json.loads(sys.argv[1]))
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        result = dict(ok=False, error_type=type(error).__name__, detail=str(error)[:500])
    print(json.dumps(result, ensure_ascii=True))
