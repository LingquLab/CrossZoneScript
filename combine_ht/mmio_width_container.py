"""Qualify u64 addressing, then compare one-WQE u32/u64 doorbells in fresh cases."""
import copy
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

ROOT = Path(__file__).resolve().parent
BASE = Path("/work/cases/8p-bs16-a0840f02-a1/send-only")
out = {"ok": False, "diagnostic_only": True, "source_revision": "a0840f023d7c20089ae0aec0057967fed74bd692", "cases": []}
snapshot = json.loads(Path("/work/environment.snapshot.json").read_text())
original = json.loads((BASE / "workload.json").read_text())


def run_case(name, gm64, bits):
    folder = ROOT / name
    folder.mkdir()
    source = folder / "probe.cpp"
    source.write_text("#define PROBE_GM64 %d\n#define PROBE_STORE_BITS %d\n" % (gm64, bits) + (ROOT / "mmio_width_probe.cpp").read_text())
    binary = folder / "probe.o"
    with (folder / "build.log").open("wb") as log:
        sst.build_kernel(snapshot["settings"], log, source=source, binary=binary)
    work = {"workload_config": {}} if gm64 else copy.deepcopy(original)
    marker = 0x20000000 + 1536 if gm64 else 3 * sst.rt.GM_BYTES + 0x2F0000000
    poison = folder / "poison.bin"
    poison.write_bytes(bytes([0xCC]) * (32768 if gm64 else 32))
    template_offset = 0
    if not gm64:
        prefill = (BASE / "input_bin/rank3/prefill.bin").read_bytes()
        _, _, groups, chunks = struct.unpack_from("<4I", prefill)
        assert groups == 1 and chunks == 4, "unexpected_prefill_stage_layout"
        entries = [struct.unpack_from("<4Q", prefill, 512 + (2 * 4 + stage) * 32) for stage in range(4)]
        template_offset = next(entry[0] for entry in entries if entry[1] > 0)
    for rank in range(8):
        base = rank * sst.rt.GM_BYTES
        old = original["workload_config"]["chip" + str(rank)]
        params = {} if gm64 else {k: v for k, v in old["kernel_param"].items() if "input_bin" in v}
        image = 0 if gm64 else old["kernel_param"]["prefill"]["value"] + template_offset
        info = 0 if gm64 else old["kernel_param"]["fullmesh_info"]["value"]
        for offset, (key, value) in enumerate((("image", image), ("info", info), ("marker", marker), ("gm", 0x20000000))):
            params[key] = dict(value=value, param_offset=str(offset * 8), param_type="11")
        params["rank"] = dict(value=rank, param_offset="32", param_type="5")
        outputs = {} if gm64 else {"workspace": old["output_param"]["workspace"]}
        if rank == (0 if gm64 else 3):
            address, size = (0x20000000, 32768) if gm64 else (marker, 32)
            params["load_probe"] = dict(value=address, param_offset="40", param_type="11", input_bin=str(poison))
            outputs["probe"] = dict(addr=address, size=size)
        work["workload_config"]["chip" + str(rank)] = dict(gm_size=sst.rt.GM_BYTES,
            sys_va_base_addr=base, ub_base_addr=0x80000, stack_base_addr=0x100000,
            param_base_addr=base + 0x80000000, block_dim=1, subblock_dim=1,
            kernel_file=str(binary), vec_func=sst.ENTRY, kernel_param=params,
            bin_param=dict(addr=base + 0x40000000, size=max(0x26C40, sst.fixtures.align(binary.stat().st_size))), output_param=outputs)
    with (folder / "model.log").open("w") as log, sst.redirect_stdout(log):
        sst.write_model(folder, snapshot, work, 8)
    command = sst.shell_command(snapshot["settings"], "sst", [snapshot["settings"]["sst_bin"], "-n", "32", folder / "test_UBusSim.py"])
    start = time.monotonic()
    timed_out = False
    with (folder / "sim.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=folder, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            process.wait(timeout=150)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=15)
    text = (folder / "sim.log").read_text(errors="replace")
    result = dict(name=name, qualified=False, bits=bits, simulator_exit=process.returncode,
        timed_out=timed_out, wall_s=time.monotonic() - start, report=str(folder), object_sha256=sst.checksum(binary),
        completions=[m.group(0) for m in sst.SIM_TIME.finditer(text)][-1:],
        fatals=[line[:450] for line in text.splitlines() if "FATAL:" in line][:2])
    if process.returncode == 0 and result["completions"] and not timed_out:
        raw = sst.read_output(folder, 0 if gm64 else 3, "probe", 0x20000000 if gm64 else marker, 32768 if gm64 else 32)
        expected = bytearray(poison.read_bytes())
        if gm64:
            struct.pack_into("<Q", expected, 0, 0x2468ACE013579BDF)
            struct.pack_into("<3I", expected, 1536, 0, 0, 0)
        else:
            struct.pack_into("<3I", expected, 0, 0, 1, 1)
        result["marker_and_guards_match"] = raw == expected
        result["qualified"] = raw == expected
        if not gm64:
            specs, _ = sst.fixtures.prepare(8, 16, 512, 16, 4, 40, 64)
            changes = []
            checks = True
            for rank, spec in enumerate(specs):
                initial, oracle, _ = spec.serialize()
                location = work["workload_config"]["chip" + str(rank)]["output_param"]["workspace"]
                actual = sst.read_output(folder, rank, "workspace", location["addr"], location["size"])
                before = initial["workspace"]
                blocks = sorted({i // 512 for i, (a, b) in enumerate(zip(actual, before)) if a != b})
                for block in blocks:
                    lo = block * 512
                    checks = checks and actual[lo:lo+512] == oracle[lo:lo+512]
                    changes.append(dict(rank=rank, offset=lo))
            result["payload_blocks"] = changes
            result["payload_matches_oracle"] = checks and len(changes) == 1
            result["qualified"] = result["qualified"] and result["payload_matches_oracle"]
    (folder / "run.json").write_text(json.dumps(result, indent=2))
    out["cases"].append(result)
    (ROOT / "summary.json").write_text(json.dumps(out, indent=2))
    return result


try:
    assert json.loads(Path("/work/revisions.json").read_text())["candidate"] == out["source_revision"]
    assert sst.checksum(sst.KERNEL) == "519c9fba13122634af8f58c50947e1765e1627b6fa6263a312116fbce0bfb790"
    sst.verify_environment(snapshot)
    gm = run_case("gm64", 1, 64)
    if gm["qualified"]:
        control = run_case("mmio32", 0, 32)
        candidate = run_case("mmio64", 0, 64)
        out["ok"] = candidate["qualified"]
except Exception as error:
    out["error_type"] = type(error).__name__
    out["detail"] = str(error)[:400]
    out["build_errors"] = [line[:350] for path in ROOT.glob("*/build.log")
        for line in path.read_text(errors="replace").splitlines() if "error:" in line][:4]
finally:
    (ROOT / "summary.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out))
sys.exit(0 if out["ok"] else 1)
