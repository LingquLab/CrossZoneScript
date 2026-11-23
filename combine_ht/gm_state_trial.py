# Copyright (c) 2026 TileXR Project.
"""Test ordinary GM access for core-owned queue state in a separate HT build.

Keeps all SIMD WQE generation, MTE3 publication and st_dev doorbells unchanged.
The local candidate adds no device loops or synchronization; baseline sources
and binaries remain intact and its result is reported as a local trial.
"""

import json
from pathlib import Path
import re
import shutil
from tempfile import mkdtemp

import sst


HELPERS = """
__aicore__ inline uint32_t SimulatorQueueLoad(__gm__ uint32_t *address, int16_t offset)
{
    return address[offset];
}
__aicore__ inline void SimulatorQueueStore(uint32_t value, __gm__ uint32_t *address, int16_t offset)
{
    address[offset] = value;
}
"""


def patch(text, prefix):
    loads = text.count("ld_dev(")
    text = text.replace("ld_dev(", prefix + "SimulatorQueueLoad(")
    stores = 0

    def replace(match):
        nonlocal stores
        value, address = match.groups()
        if not re.search(r"->(?:headAddr|tailAddr|wqeCntAddr)$", address.strip()):
            return match[0]
        stores += 1
        return prefix + "SimulatorQueueStore(" + value + ", reinterpret_cast<__gm__ uint32_t*>(" + address + "), 0);"

    text = re.sub(r"st_dev\((.*?),\s*reinterpret_cast<__gm__ uint32_t\s*\*>\(([^()]*)\),\s*0\);", replace, text, flags=re.S)
    return text, dict(loads=loads, stores=stores)


def main():
    case = Path(json.loads((sst.WORK / "progress.json").read_text())["case"])
    assert case.parent == sst.WORK / "cases"
    snapshot = json.loads((case / "send-only/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    report = Path(mkdtemp(prefix="gm-state-trial-", dir=case))
    source_dir = sst.WORK / "candidate/src/moonep/combine_ht/kernels"
    source = report / "combine_ht.cpp"
    shutil.copyfile(source_dir / "combine_ht.cpp", source)
    header, kernel_changes = patch((source_dir / "combine_ht_kernel.h").read_text(), "TileXR::")
    begin = header.index("// Reuse the EP UBX Gather/Mull/Scatter pattern.")
    end = header.index("#else\nconstexpr uint32_t kWqeWords", begin)
    header = header[:begin] + '#include "simd_wqe_interleave.h"\n' + header[end:]
    shutil.copyfile(Path(__file__).with_name("simd_wqe_interleave.h"), report / "simd_wqe_interleave.h")
    (report / "combine_ht_kernel.h").write_text(header)
    api = sst.WORK / "candidate/include/tilexr/comm/udma/device/api.h"
    patched, api_changes = patch(api.read_text(), "")
    patched = patched.replace("namespace TileXR {", "namespace TileXR {\n" + HELPERS, 1)
    override = report / "include/tilexr/comm/udma/device/api.h"
    override.parent.mkdir(parents=True)
    override.write_text(patched)
    assert "st_dev(curHead, reinterpret_cast<__gm__ uint32_t*>(qpCtxEntry->dbAddr), 0);" in patched
    assert "TileXR::UDMARingDoorbell(head, sq);" in header
    binary = report / "gm_state_candidate.o"
    with (report / "build.log").open("wb") as log:
        sst.build_kernel(snapshot["settings"], log, source=source, binary=binary, include_overrides=[report / "include"])
    specs, _ = sst.fixtures.prepare(8, 4, 256, 16, 4, 40, 64)
    prepared = [spec.serialize() for spec in specs]
    stages = []
    original = sst.KERNEL
    result = dict(report=str(report), local_candidate=True, wqe_builder="simd_interleave_shift_address_h256", baseline_kernel_sha256=sst.checksum(original),
                  candidate_kernel_sha256=sst.checksum(binary), changes=dict(kernel=kernel_changes, api=api_changes), stages=stages)
    sst.KERNEL = binary
    try:
        state = sst.run_phase(report / "send-only", 1, specs, prepared, snapshot,
                              dict(ranks=8, threads=16, stage_timeout=420), stages)
        result.update(send_verified=True, verified_ranks=len(state))
    except (OSError, ValueError) as error:
        result.update(send_verified=False, failure=str(error)[:500])
    finally:
        sst.KERNEL = original
    log_path = report / "send-only/sim.log"
    if log_path.is_file() and not result.get("send_verified"):
        lines = log_path.read_text(errors="replace").splitlines()
        result["first_fatal"] = [line[line.index("FATAL:"):][:700] for line in lines if "FATAL:" in line][:1]
        result["heap_errors"] = [line[:500] for line in lines if re.search(r"malloc|corrupt|double.free|unaligned|assertion", line, re.I)][:2]
    sst.write_json(report / "trial.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
