"""Qualify B32 model address adaptation with exact buffer and guard checks."""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import time

sys.path.insert(0, "/work")
import sst
from case_tools import extract_ranges


def main():
    folder = Path(__file__).resolve().parent
    assert folder.name in ("stdev-address-a1", "stdev-address-trace-a1", "stdev-address-high-a1")
    tracing = folder.name != "stdev-address-a1"
    high_address = folder.name == "stdev-address-high-a1"
    assert not (folder / "run.json").exists(), "probe_already_attempted"
    config = json.loads(Path("/work/environment.snapshot.json").read_text())
    binary = folder / "probe.o"
    if tracing:
        original = folder.parent / "stdev-address-a1/probe.o"
        assert sst.checksum(original) == "4a3749e99e95ec6b98a0077a478157039f2c9f181893c8d59c54d2b2ec9c49fd"
        shutil.copyfile(original, binary)
        configure = sst.configure
        def trace_config(sources, args):
            args.instruction_dump_chips = [0]
            return configure(sources, args)
        sst.configure = trace_config
    else:
        with (folder / "build.log").open("wb") as log:
            sst.build_kernel(config["settings"], log, source=folder / "address_probe.cpp", binary=binary)
    poison = folder / "poison.bin"
    poison.write_bytes(bytes([0xCC]) * 32768)
    workload = {"workload_config": {}}
    addresses = (0x2000000, 0x10000000, 0x400000) if high_address else (0x200000, 0x1000000, 0x40000)
    for rank in range(8):
        base = rank * sst.rt.GM_BYTES
        params = {"dst": dict(value=base + addresses[0], param_offset="0", param_type="11"),
                  "rank": dict(value=rank, param_offset="8", param_type="5")}
        outputs = {}
        if rank == 0:
            for index, address in enumerate(addresses):
                params["load" + str(index)] = dict(value=address, param_offset=str(16 + 8 * index),
                                                  param_type="11", input_bin=str(poison))
                outputs["region" + str(index)] = dict(addr=address, size=32768)
        workload["workload_config"]["chip" + str(rank)] = dict(
            gm_size=sst.rt.GM_BYTES, sys_va_base_addr=base, ub_base_addr=0x80000,
            stack_base_addr=0x100000, param_base_addr=base + 0x80000000,
            block_dim=1, subblock_dim=1, kernel_file=str(binary), vec_func=sst.ENTRY,
            kernel_param=params, bin_param=dict(addr=base + 0x40000000,
            size=max(0x26C40, sst.fixtures.align(binary.stat().st_size))), output_param=outputs)
    with (folder / "model.log").open("w") as log, sst.redirect_stdout(log):
        sst.write_model(folder, config, workload, 8)
    command = sst.shell_command(config["settings"], "sst", [config["settings"]["sst_bin"], "-n", "32", folder / "test_UBusSim.py"])
    started = time.monotonic()
    timed_out = False
    with (folder / "sim.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=folder, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=15)
    result = dict(ok=False, diagnostic_only=True, image_version="V1.1.0", report=str(folder),
                  simulator_exit=process.returncode, timed_out=timed_out,
                  wall_s=time.monotonic() - started, probe_sha256=sst.checksum(binary))
    text = (folder / "sim.log").read_text(errors="replace")
    result["completions"] = [m.group(0) for m in sst.SIM_TIME.finditer(text)][-1:]
    result["fatals"] = [line[:300] for line in text.splitlines() if "FATAL:" in line][:2]
    result["checks"] = []
    if process.returncode == 0 and not timed_out:
        for index, address in enumerate(addresses):
            expected = bytearray(poison.read_bytes())
            values = ({0: 0x13579BDF, 512: 0x2468ACE0, 1536: 0x12345678}, {8192: 0x31415926}, {})[index]
            for offset, value in values.items():
                struct.pack_into("<I", expected, offset, value)
            actual = sst.read_output(folder, 0, "region" + str(index), address, 32768)
            changed = [i for i, value in enumerate(actual) if value != 0xCC]
            entry = dict(address=hex(address), exact_match=actual == expected,
                mismatched_bytes=sum(a != b for a, b in zip(actual, expected)), size=len(actual),
                changed_bytes=len(changed), first_changes=[dict(offset=i, value=actual[i]) for i in changed[:20]])
            entry["die_samples"] = []
            for die in (1, 2):
                path = folder / "bin" / ("david_0_die%d_region%d.bin" % (die, index))
                raw = extract_ranges([path], {"region": (address, 32768)})["region"]
                entry["die_samples"].append(dict(die=die, bytes=len(raw),
                    samples={str(i): raw[i:i+4].hex() for i in (0, 512, 1024, 1536, 8192)}))
            result["checks"].append(entry)
        result["ok"] = bool(result["completions"]) and all(x["exact_match"] for x in result["checks"])
    if tracing:
        result["address_qualification_passed"] = result["ok"]
        result["instructions"] = []
        for path in sorted(folder.rglob("*sudetail.dump")):
            for line in path.read_text(errors="replace").splitlines():
                if "scalarStDev" in line:
                    fields = {}
                    for key in ("PC", "RAW", "xnValue", "xdValue", "dataType"):
                        match = re.search(r"\b" + key + r"\s*=\s*([0-9A-Za-z]+)", line)
                        if match:
                            fields[key] = match.group(1)
                    result["instructions"].append(fields)
                if len(result["instructions"]) >= 3:
                    break
            if len(result["instructions"]) >= 3:
                break
        # Trace collection is a separate diagnostic; retain the negative guard result.
        result["ok"] = process.returncode == 0 and bool(result["completions"]) and len(result["instructions"]) == 3
    (folder / "run.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    try:
        result = main()
    except Exception as error:
        result = dict(ok=False, error_type=type(error).__name__, detail=str(error)[:240])
    print(json.dumps(result))
    sys.exit(0 if result["ok"] else 1)
