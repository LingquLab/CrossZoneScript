# Copyright (c) 2026 TileXR Project.
"""Capture fixed scalar control fields before ld_dev in an isolated kernel.

The diagnostic kernel has no device loop, synchronization or transport calls.
It snapshots values already consumed by Send; no production path is modified.
"""

from contextlib import redirect_stdout
import json
from pathlib import Path
import shutil
import struct
import subprocess
from tempfile import mkdtemp
import time

import sst


SOURCE = r'''
/* Copyright (c) 2026 TileXR Project.
 * Snapshots fixed Send control pointers before any queue-state dereference.
 * The diagnostic runner owns the output; no transport or synchronization runs.
 */
#include "combine_ht_kernel.h"
extern "C" __global__ __aicore__ void tilexr_moonep_combine_ht_kernel(TileXRCombineHT::KernelArgs args)
{
    if (GetBlockIdx() != 0) return;
    auto *output = reinterpret_cast<__gm__ uint64_t *>(args.status);
    auto *info = reinterpret_cast<__gm__ TileXR::UDMAInfo *>(args.fullmesh_info);
    output[0] = reinterpret_cast<uint64_t>(info);
    output[1] = info->qpNum;
    output[2] = info->sqPtr;
    auto *sq = TileXR::UDMAGetWQCtx(info, 0, 0);
    output[3] = reinterpret_cast<uint64_t>(sq);
    if (!sq) return;
    output[4] = sq->headAddr;
    output[5] = sq->wqeCntAddr;
    output[6] = sq->bufAddr;
}
'''


def main():
    case = Path(json.loads((sst.WORK / "progress.json").read_text())["case"])
    assert case.parent == sst.WORK / "cases"
    snapshot = json.loads((case / "send-only/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    report = Path(mkdtemp(prefix="pointer-probe-", dir=case))
    source = report / "pointer_probe.cpp"
    source.write_text(SOURCE)
    shutil.copyfile(sst.WORK / "candidate/src/moonep/combine_ht/kernels/combine_ht_kernel.h", report / "combine_ht_kernel.h")
    binary = report / "pointer_probe.o"
    env = snapshot["settings"]
    with (report / "build.log").open("wb") as log:
        sst.build_kernel(env, log, source=source, binary=binary)
    specs, _ = sst.fixtures.prepare(8, 4, 256, 16, 4, 40, 64)
    prepared = [spec.serialize() for spec in specs]
    original = sst.KERNEL
    sst.KERNEL = binary
    run = report / "run"
    try:
        workload = sst.create_workload(run, specs, prepared, 1)
    finally:
        sst.KERNEL = original
    for chip in workload["workload_config"].values():
        chip["output_param"] = {"status": chip["output_param"]["status"]}
    with (report / "model.log").open("w") as log, redirect_stdout(log):
        sst.write_model(run, snapshot, workload, 8)
    start = time.monotonic()
    with (run / "sim.log").open("wb") as log:
        try:
            process = subprocess.run(sst.shell_command(env, "sst", [env["sst_bin"], "-n", "16", run / "test_UBusSim.py"]), cwd=run, stdout=log, stderr=subprocess.STDOUT, timeout=240)
            code = process.returncode
        except subprocess.TimeoutExpired:
            code = 124
    result = dict(report=str(report), diagnostic_only=True, returncode=code, wall_s=round(time.monotonic()-start, 3), ranks=[])
    if not code:
        for rank in range(8):
            output = workload["workload_config"][f"chip{rank}"]["output_param"]["status"]
            raw = sst.read_output(run, rank, "status", output["addr"], output["size"])
            values = struct.unpack_from("<7Q", raw)
            q = sst.rt.queue_addresses(rank, 8, 0, 0)
            base = rank * sst.rt.GM_BYTES
            expected = (base+sst.rt.BASES["udma_info"], 40, base+sst.rt.BASES["sq_ctx"], base+sst.rt.BASES["sq_ctx"], q["sq_head"], q["count"], q["sq"])
            result["ranks"].append(dict(rank=rank, actual=[hex(value) for value in values], expected=[hex(value) for value in expected], match=values==expected))
    sst.write_json(report / "analysis.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
