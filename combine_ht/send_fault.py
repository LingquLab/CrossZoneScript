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
        objdump = shutil.which("objdump")
        if objdump:
            disassembly = subprocess.run([objdump, "-d", "--start-address=" + hex(offset - 8),
                                          "--stop-address=" + hex(offset + 8), path], capture_output=True, text=True, timeout=20)
            entry["instructions"] = [line.strip() for line in disassembly.stdout.splitlines()
                                     if re.match(r"^\s*[0-9a-f]+:\s+[0-9a-f]{8}\s", line)][:4]
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
    library = case / "sst_trace"
    source = Path(__file__).with_name("native_trace.c")
    compiler = shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        raise RuntimeError("native compiler unavailable")
    with (case / "build.log").open("wb") as log:
        subprocess.run([compiler, "-Wall", "-Wextra", "-O2",
                        str(source), "-o", str(library)], stdout=log,
                       stderr=subprocess.STDOUT, timeout=30, check=True)
    preflight = subprocess.run([str(library), str(case / "preflight.txt"), "/bin/true"], capture_output=True, timeout=10)
    if preflight.returncode:
        print(json.dumps(dict(report=str(case), ptrace_preflight_exit=preflight.returncode, run_skipped=True)))
        return
    env = snapshot["settings"]
    fault_path = case / "fault-addresses.txt"
    cmd = sst.shell_command(env, "sst", [str(library), str(fault_path), "/opt/sim/local/sstcore/libexec/sstsim.x", "-n", "16",
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
    result["ptrace_preflight_exit"] = 0
    result["sst_launcher"] = "/opt/sim/local/sstcore/libexec/sstsim.x"
    lifecycle = Path(str(fault_path) + ".lifecycle")
    result["recorder_lifecycle"] = lifecycle.read_text().splitlines()[-12:] if lifecycle.exists() else []
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
    registers = Path(str(fault_path) + ".regs")
    if registers.is_file():
        result["registers"] = json.loads(registers.read_text())
    configs = sst.yaml.safe_load((case / "UBusSim_config/UBusSim_simulator_config.yml").read_text())
    result["architecture_config"] = configs.get("architecture_config", {})
    result["baseline_report"] = str(BASELINE)
    result["kernel_sha256"] = sst.checksum(sst.KERNEL)
    sst.write_json(case / "diagnosis.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
