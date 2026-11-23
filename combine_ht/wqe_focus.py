# Copyright (c) 2026 TileXR Project.
"""Snapshot SIMD-built WQEs with transport submission disabled for diagnosis.

Compiles a separate diagnostic object from the pinned source, omitting only the
doorbell and final CQ wait. Uses the existing snapshot ABI without adding device
loops or synchronization. Host validation compares descriptors with the fixture.
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


def main():
    case = Path(json.loads((sst.WORK / "progress.json").read_text())["case"])
    assert case.parent == sst.WORK / "cases"
    snapshot = json.loads((case / "send-only/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    report = Path(mkdtemp(prefix="wqe-focus-", dir=case))
    source_dir = sst.WORK / "candidate/src/moonep/combine_ht/kernels"
    header = (source_dir / "combine_ht_kernel.h").read_text()
    for old, new in (("TileXR::UDMARingDoorbell(head, sq);", "(void)sq; // Diagnostic: no transport submission."),
                     ("status = batch.Quiet();", "status = TileXR::TILEXR_UDMA_STATUS_SUCCESS; // Diagnostic: no CQ wait.")):
        assert header.count(old) == 1
        header = header.replace(old, new)
    (report / "combine_ht_kernel.h").write_text(header)
    source = report / "combine_ht.cpp"
    shutil.copyfile(source_dir / "combine_ht.cpp", source)
    binary = report / "wqe_capture.o"
    with (report / "build.log").open("wb") as log:
        sst.build_kernel(snapshot["settings"], log, source=source, binary=binary)
    specs, routes = sst.fixtures.prepare(8, 4, 256, 16, 4, 40, 64)
    prepared = [rank.serialize() for rank in specs]
    run = report / "capture"
    original_binary = sst.KERNEL
    sst.KERNEL = binary
    try:
        workload = sst.create_workload(run, specs, prepared, 1)
    finally:
        sst.KERNEL = original_binary
    for rank, spec in enumerate(specs):
        chip = workload["workload_config"][f"chip{rank}"]
        address = rank * sst.rt.GM_BYTES + 0x280000000
        size = 8 * (spec.backward_capacity + spec.chunks) * 64
        image = run / "input_bin" / f"rank{rank}" / "snapshot.bin"
        image.write_bytes(b"\xcd" * size)
        chip["kernel_param"]["wqe_snapshot"]["value"] = address
        chip["kernel_param"]["load_snapshot"] = dict(value=address, param_offset="512", param_type="11", input_bin=str(image))
        chip["output_param"]["wqe_snapshot"] = dict(addr=address, size=size)
    with (report / "model.log").open("w") as log, redirect_stdout(log):
        sst.write_model(run, snapshot, workload, 8)
    env = snapshot["settings"]
    command = sst.shell_command(env, "sst", [env["sst_bin"], "-n", "16", run / "test_UBusSim.py"])
    start = time.monotonic()
    with (run / "sim.log").open("wb") as log:
        try:
            process = subprocess.run(command, cwd=run, stdout=log, stderr=subprocess.STDOUT, timeout=420)
            code = process.returncode
        except subprocess.TimeoutExpired:
            code = 124
    result = dict(report=str(report), diagnostic_only=True, returncode=code, wall_s=round(time.monotonic()-start, 3),
                  baseline_kernel_sha256=sst.checksum(original_binary), diagnostic_kernel_sha256=sst.checksum(binary))
    checked, mismatches = 0, []
    if code == 0:
        for rank, spec in enumerate(specs):
            chip = workload["workload_config"][f"chip{rank}"]
            output = chip["output_param"]["wqe_snapshot"]
            raw = sst.read_output(run, rank, "wqe_snapshot", output["addr"], output["size"])
            own_input = chip["kernel_param"]["input"]["value"]
            for gateway, stages in enumerate(spec.backward):
                if gateway == rank:
                    continue
                peer_chip = workload["workload_config"][f"chip{gateway}"]
                remote_base = peer_chip["kernel_param"]["workspace"]["value"]
                ordinal = 0
                for stage, records in enumerate(stages):
                    expected = []
                    for source_rank, source_row, packed_row in records:
                        expected.append((ordinal, own_input + source_row * 512,
                                         remote_base + spec.gather_offset + (packed_row & 0xffffffff) * 512, 512))
                        ordinal += 1
                    if records:
                        expected.append((spec.backward_capacity + stage,
                            chip["kernel_param"]["workspace"]["value"] + (3 * 64 + gateway) * 512,
                            remote_base + (3 * 64 + 96 + stage * 8 + rank) * 512, 8))
                    for row, source, destination, length in expected:
                        offset = (gateway * (spec.backward_capacity + spec.chunks) + row) * 64
                        words = struct.unpack_from("<16I", raw, offset)
                        actual = (words[14] | words[15] << 32, words[10] | words[11] << 32, words[12])
                        checked += 1
                        if actual != (source, destination, length):
                            mismatches.append(dict(rank=rank, gateway=gateway, stage=stage, row=row,
                                expected=[hex(source),hex(destination),length], actual=[hex(actual[0]),hex(actual[1]),actual[2]],
                                source_host=actual[0] // sst.rt.GM_BYTES, destination_host=actual[1] // sst.rt.GM_BYTES))
        sst.write_json(report / "mismatches.json", mismatches)
    result.update(checked_wqes=checked, mismatch_count=len(mismatches), first_mismatches=mismatches[:3])
    sst.write_json(report / "diagnosis.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
