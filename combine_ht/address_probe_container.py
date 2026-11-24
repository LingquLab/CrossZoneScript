"""Qualify B32 model address adaptation with exact buffer and guard checks."""
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time

sys.path.insert(0, "/work")
import sst


def main():
    folder = Path(__file__).resolve().parent
    assert folder.name == "stdev-address-a1"
    assert not (folder / "run.json").exists(), "probe_already_attempted"
    config = json.loads(Path("/work/environment.snapshot.json").read_text())
    binary = folder / "probe.o"
    with (folder / "build.log").open("wb") as log:
        sst.build_kernel(config["settings"], log, source=folder / "address_probe.cpp", binary=binary)
    poison = folder / "poison.bin"
    poison.write_bytes(bytes([0xCC]) * 32768)
    workload = {"workload_config": {}}
    addresses = (0x200000, 0x1000000, 0x40000)
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
            result["checks"].append(dict(address=hex(address), exact_match=actual == expected,
                mismatched_bytes=sum(a != b for a, b in zip(actual, expected)), size=len(actual)))
        result["ok"] = bool(result["completions"]) and all(x["exact_match"] for x in result["checks"])
    (folder / "run.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    try:
        result = main()
    except Exception as error:
        result = dict(ok=False, error_type=type(error).__name__, detail=str(error)[:240])
    print(json.dumps(result))
    sys.exit(0 if result["ok"] else 1)
