# Copyright (c) 2026 TileXR Project.
"""Run isolated SIMD instruction probes through the same HT control path.

The diagnostic builder adds no device loop or synchronization. Submission and
the final CQ wait are omitted, so results establish instruction compatibility
only; they do not validate Send data or performance.
"""

from contextlib import redirect_stdout
import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import mkdtemp
import time

import sst


PROTOTYPE = """
template <bool Signal, bool Sample = false>
__aicore__ inline void BuildPutWqes(LocalTensor<uint8_t> wqes, LocalTensor<uint8_t> header,
    LocalTensor<int64_t> records, uint32_t count, uint32_t head,
    uint64_t input, uint64_t remote, uint32_t bytes, uint32_t rowBegin, uint32_t rowEnd)
{
    BODY
}
"""


def main():
    case = Path(json.loads((sst.WORK / "progress.json").read_text())["case"])
    assert case.parent == sst.WORK / "cases"
    candidate = case / "gm-state-trial-p_rm8s8n"
    snapshot = json.loads((candidate / "send-only/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    report = Path(mkdtemp(prefix="vector-op-probe-", dir=case))
    specs, _ = sst.fixtures.prepare(8, 4, 256, 16, 4, 40, 64)
    prepared = [spec.serialize() for spec in specs]
    operations = {
        "no_vec": "",
        "vec_store": "DataCopy<uint32_t>(destination, a, all);",
        "vec_post_store": "DataCopy<uint32_t, PostLiteral::POST_MODE_UPDATE>(destination, a, 64, all);",
        "vec_gather": "DataCopyGather(a, source, index, all); DataCopy<uint32_t>(destination, a, all);",
        "vec_interleave": "Interleave(a, b, a, b); DataCopy<uint32_t>(destination, a, all);",
    }
    results = []
    original_binary = sst.KERNEL
    for name, operation in operations.items():
        folder = report / name
        folder.mkdir()
        header = (candidate / "combine_ht_kernel.h").read_text()
        body = ""
        if operation:
            body = """auto *destination = reinterpret_cast<__ubuf__ uint32_t *>(wqes.GetPhyAddr());
    auto *source = reinterpret_cast<__ubuf__ uint32_t *>(header.GetPhyAddr());
    __VEC_SCOPE__ {
        using namespace MicroAPI;
        RegTensor<uint32_t> a, b, index;
        auto all = CreateMask<uint32_t>();
        Duplicate(a, 0x13579bdfU);
        Duplicate(b, 0x2468ace0U);
        Duplicate(index, 0U);
        OPERATION
    }""".replace("OPERATION", operation)
        header = header.replace('#include "simd_wqe_interleave.h"', PROTOTYPE.replace("BODY", body))
        header = header.replace("TileXR::UDMARingDoorbell(head, sq);", "(void)sq;")
        header = header.replace("status = batch.Quiet();", "status = TileXR::TILEXR_UDMA_STATUS_SUCCESS;")
        (folder / "combine_ht_kernel.h").write_text(header)
        source = folder / "combine_ht.cpp"
        shutil.copyfile(candidate / "combine_ht.cpp", source)
        binary = folder / "probe.o"
        with (folder / "build.log").open("wb") as log:
            sst.build_kernel(snapshot["settings"], log, source=source, binary=binary, include_overrides=[candidate / "include"])
        run = folder / "run"
        sst.KERNEL = binary
        try:
            workload = sst.create_workload(run, specs, prepared, 1)
        finally:
            sst.KERNEL = original_binary
        with (folder / "model.log").open("w") as log, redirect_stdout(log):
            sst.write_model(run, snapshot, workload, 8)
        env = snapshot["settings"]
        start = time.monotonic()
        with (run / "sim.log").open("wb") as log:
            try:
                process = subprocess.run(sst.shell_command(env, "sst", [env["sst_bin"], "-n", "16", run / "test_UBusSim.py"]),
                                         cwd=run, stdout=log, stderr=subprocess.STDOUT, timeout=65)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 124
        record = dict(operation=name, report=str(folder), returncode=code, wall_s=round(time.monotonic()-start, 3),
                      kernel_sha256=sst.checksum(binary))
        lines = (run / "sim.log").read_text(errors="replace").splitlines()
        errors = []
        for line in lines:
            match = re.search(r"FATAL:|corrupt|unaligned|assertion|malloc\(\):", line, re.I)
            if match:
                errors.append(line[max(0, match.start()-40):match.start()+450])
        record["first_errors"] = errors[:1]
        record["simulation_completed"] = not code and any(sst.SIM_TIME.search(line) for line in lines)
        if record["simulation_completed"]:
            statuses = []
            for rank in range(8):
                output = workload["workload_config"][f"chip{rank}"]["output_param"]["status"]
                raw = sst.read_output(run, rank, "status", output["addr"], output["size"])
                statuses.append(not any(raw))
            record["zero_status_ranks"] = sum(statuses)
        results.append(record)
        sst.write_json(report / "analysis.json", results)
    print(json.dumps(dict(report=str(report), diagnostic_only=True, scope="instruction compatibility; no submission or Send correctness claim", operations=results), ensure_ascii=True))


if __name__ == "__main__":
    main()
