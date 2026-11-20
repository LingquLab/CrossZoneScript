# Copyright (c) 2026 TileXR Project.
"""Run a minimal Cast/Add/MTE3 probe in the retained perf environment."""

from contextlib import redirect_stdout
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import struct
import subprocess
import time

import castloc_results
import lane_results
import sst


def build_castloc_barriers(env, case, kernel):
    source = case / "castloc_probe.cpp"
    original = (sst.WORK / source.name).read_text()
    cast = "    Cast(bf16, fp32, RoundMode::CAST_RINT, kElements);\n"
    barrier = "    PipeBarrier<PIPE_V>();\n"
    if original.count(cast + barrier) != 1:
        raise ValueError("could not identify the CAST_RINT and its original barrier")
    source.write_text(original.replace(cast, barrier * 16 + cast + barrier * 16))
    with (case / "build.log").open("wb") as log:
        sst.build_kernel(env, log, source=source, binary=kernel)

    result = {"ok": True, "file": str(kernel.relative_to(sst.WORK)), "size": kernel.stat().st_size,
              "sha256": sst.checksum(kernel), "source_barriers_before_after": [16, 17]}
    tools = Path(env["compiler"]).parent
    for objdump in sorted(tools.glob("*objdump*")):
        try:
            decoded = subprocess.run(sst.shell_command(env, "cann", [objdump, "-d", kernel]),
                                     capture_output=True, timeout=12)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if decoded.returncode:
            continue
        assembly = case / "castloc_probe.disasm"
        assembly.write_bytes(decoded.stdout)
        instructions = decoded.stdout.decode(errors="replace").lower().splitlines()
        casts = [index for index, line in enumerate(instructions) if re.search(r"\bvconv\b", line)]
        if not casts:
            continue
        barriers = [index for index, line in enumerate(instructions)
                    if re.search(r"\bpipe_barrier\b.*\bpipe_v\b", line)]
        result["vector_barriers_in_elf"] = len(barriers)
        result["vector_barriers_near_each_vconv"] = [
            [sum(0 < index - row <= 40 for row in barriers),
             sum(0 < row - index <= 40 for row in barriers)] for index in casts
        ]
        break
    return result


def run(args):
    config = json.loads((sst.WORK / "environment.snapshot.json").read_text())
    env = sst.verify_environment(config)
    kind = args["stage"].split("-")[0]
    entry = "tilexr_" + kind + "_probe"
    compile_only = args["stage"] == "castloc-barrier-build"
    label = "castloc-pipebarrier16" if compile_only else kind
    case = sst.WORK / "diagnostics" / (label + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    case.mkdir(parents=True)
    kernel = case / (entry + ".o")
    if compile_only:
        return build_castloc_barriers(env, case, kernel)
    with (case / "build.log").open("wb") as log:
        sst.build_kernel(env, log, source=sst.WORK / (kind + "_probe.cpp"), binary=kernel)
    inputs = case / "input.bin"
    zero = case / "zero.bin"
    output_bytes = {"lane": lane_results.OUTPUT_BYTES, "castloc": castloc_results.OUTPUT_BYTES}.get(kind, 2048)
    if kind == "castloc":
        input_bytes, fp32, reference = castloc_results.inputs(sst.fixtures.bf16)
        inputs.write_bytes(input_bytes)
        zero.write_bytes(bytes(output_bytes))
    elif kind == "lane":
        inputs.write_bytes(bytes(512))
        zero.write_bytes(bytes(output_bytes))
    elif kind == "bf16":
        values_a = [(16 + i % 16) / 16 for i in range(256)]
        values_b = [(16 + (i // 16) % 16) / 16 for i in range(256)]
        expected_copy = struct.pack("<256H", *(sst.fixtures.bf16(x) for x in values_a))
        expected_fp32 = struct.pack("<256f", *(a + b for a, b in zip(values_a, values_b)))
        expected_bf16 = struct.pack("<256H", *(sst.fixtures.bf16(a + b) for a, b in zip(values_a, values_b)))
        inputs.write_bytes(expected_copy + struct.pack("<256H", *(sst.fixtures.bf16(x) for x in values_b)))
        zero.write_bytes(bytes(2048))
    elif kind == "wqe":
        local_rows = (3, 4, 15, 18, 27, 28, 45)
        gather_rows = (0, 1, 4, 9, 12, 13, 23)
        records = b"".join(struct.pack("<3q", row, local, gather)
                           for row, (local, gather) in enumerate(zip(local_rows, gather_rows)))
        inputs.write_bytes(records.ljust(512, b"\0"))
        zero.write_bytes(bytes(2048))
    else:
        pattern = bytes(1 + (i + i // 64) % 253 for i in range(448))
        inputs.write_bytes(pattern.ljust(512, b"\0"))
        zero.write_bytes(bytes(2048))
        expected_single = bytes(384) + pattern + bytes(192)
        expected_split = expected_single
    ranks = 8
    chips = {}
    for rank in range(ranks):
        base = rank * sst.rt.GM_BYTES
        input_addr = base + 0x200000000
        output_addr = base + (0x1900000000 if kind == "mte3" else 0x200100000)
        chips[f"chip{rank}"] = dict(gm_size=sst.rt.GM_BYTES, sys_va_base_addr=base,
            ub_base_addr=0x80000, stack_base_addr=0x100000, param_base_addr=base + 0x80000000,
            block_dim=1, subblock_dim=1, kernel_file=str(kernel), vec_func=entry,
            kernel_param=dict(input=dict(value=input_addr, param_offset="0", param_type="11", input_bin=str(inputs)),
                              output=dict(value=output_addr, param_offset="8", param_type="11", input_bin=str(zero))),
            bin_param=dict(addr=base + 0x40000000, size=max(0x26C40, sst.fixtures.align(kernel.stat().st_size))),
            output_param=dict(probe=dict(addr=output_addr, size=output_bytes)))
    with (case / "model.log").open("w") as log, redirect_stdout(log):
        model = sst.write_model(case, config, {"workload_config": chips}, ranks)
    command = sst.shell_command(env, "sst", [env["sst_bin"], "-n", "8", case / "test_UBusSim.py"])
    begin = time.monotonic()
    with (case / "sim.log").open("wb") as log:
        proc = subprocess.run(command, cwd=case, stdout=log, stderr=subprocess.STDOUT,
                              timeout=args.get("stage_timeout", 150))
    elapsed = time.monotonic() - begin
    if proc.returncode:
        return dict(ok=False, report=str(case), returncode=proc.returncode, wall_s=elapsed)
    all_results = []
    die_decoder = sst.has_die_decoder(case)
    for rank, chip in enumerate(chips.values()):
        output = chip["output_param"]["probe"]
        raw = sst.read_output(case / "bin", rank, "probe", output["addr"], output["size"], die_decoder)
        if kind == "castloc":
            all_results.append({"rank": rank, **castloc_results.decode(raw, fp32, reference)})
            continue
        if kind == "lane":
            all_results.append(dict(rank=rank, trials=lane_results.decode(raw)))
            continue
        if kind == "wqe":
            conditions = []
            for trial in range(2):
                check = []
                for row, (local, gather) in enumerate(zip(local_rows, gather_rows)):
                    offset = trial * 512 + row * 64
                    word0, word1 = struct.unpack_from("<2I", raw, offset)
                    dst, length, src = struct.unpack_from("<QI4xQ", raw, offset + 40)
                    check.append((word0 & 0xffff, (word0 >> 16) & 0xff,
                                  (word1 >> 8) & 0xff, dst, length, src) == (
                                      6 + trial * 8 + row, 0x20 if row == 6 else 0, 3,
                                      0x210000000 + gather * 512, 512, 0x200000000 + local * 512))
                conditions.append(check)
            lane_values = struct.unpack_from("<56Q", raw, 1024)
            lane_check = [lane_values[i:i + 8] == tuple(0x5a50616101000000 + j for j in range(i, i + 8))
                          for i in range(0, 56, 8)]
            all_results.append(dict(rank=rank, production_v_mte3=conditions[0],
                                    v_s_and_v_mte3=conditions[1], plain_simt_lane_writes=lane_check))
            continue
        if kind == "bf16":
            samples = dict(copy=raw[:512], cast=raw[512:1024], fp32=raw[1024:])
            golden = dict(copy=expected_copy, cast=expected_bf16, fp32=expected_fp32)
        else:
            samples = dict(single=raw[:1024], split=raw[1024:])
            golden = dict(single=expected_single, split=expected_split)
        result = dict(rank=rank, mismatched_bytes={name: sum(a != b for a, b in zip(data, golden[name]))
                                                   for name, data in samples.items()})
        if kind == "bf16":
            result["cast16_first8"] = struct.unpack_from("<8H", samples["cast"])
        else:
            result["single_correct_WQEs"] = [raw[384 + p:448 + p] == pattern[p:p + 64]
                                            for p in range(0, 448, 64)]
        all_results.append(result)
    result = dict(ok=True, report=str(case), returncode=proc.returncode, wall_s=elapsed,
                  model=model, ranks=all_results)
    if kind == "bf16":
        result["expected_cast16_first8"] = struct.unpack_from("<8H", expected_bf16)
    sst.write_json(case / "report.json", result)
    return result


if __name__ == "__main__":
    import sys
    try:
        print(json.dumps(run(json.loads(sys.argv[1])), ensure_ascii=True))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(ok=False, error=type(error).__name__, detail=str(error))))
