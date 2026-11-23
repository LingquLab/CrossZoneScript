# Copyright (c) 2026 TileXR Project.
"""Build one MTE queue-state variant and validate the existing 8P BS4 Send fixture.

The baseline tree is mounted read-only. This script writes generated source,
objects and reports only under /work, leaving the original WQE builder intact.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, "/baseline")
import sst

WORK = Path("/work")


def state_loads(text, scratch):
    pattern = r"ld_dev\((reinterpret_cast<__gm__ uint32_t\s*\*>\([^)]+\)),\s*0\)"
    return re.subn(pattern, r"TileXR::SimulatorLoad(\1, " + scratch + ")", text)


def patch(folder, baseline=Path("/baseline"), builder="original"):
    original = baseline / "candidate/src/moonep/combine_ht/kernels"
    header = (original / "combine_ht_kernel.h").read_text()
    api = (baseline / "candidate/include/tilexr/comm/udma/device/api.h").read_text()
    begin = api.index("template <typename CompletionObserver>\n__aicore__ inline uint32_t UDMAPollCQUntilSqTail(")
    end = api.index("struct UDMAIgnoreCompletion", begin)
    poll = api[begin:end].replace("UDMAPollCQUntilSqTail(", "SimulatorPollCQUntilSqTail(")
    poll = poll.replace("CompletionObserver &observer)",
                        "CompletionObserver &observer, AscendC::LocalTensor<uint32_t> readScratch, "
                        "AscendC::LocalTensor<uint32_t> writeScratch)")
    poll, poll_loads = state_loads(poll, "readScratch")
    assert poll_loads == 3
    old = "UDMAPollCQUpdateInfo(cqTail, sqTail, cqCtxEntry, wqCtxEntry);"
    new = """SimulatorStore(cqTail, reinterpret_cast<__gm__ uint32_t*>(cqCtxEntry->tailAddr), writeScratch);
        SimulatorStore(sqTail, reinterpret_cast<__gm__ uint32_t*>(wqCtxEntry->tailAddr), writeScratch);
        st_dev(static_cast<uint32_t>(cqTail & 0xFFFFFFU),
            reinterpret_cast<__gm__ uint32_t*>(cqCtxEntry->dbAddr), 0);"""
    assert poll.count(old) == 1
    poll = poll.replace(old, new)
    helper = WORK / "mte_state.h"
    generated = helper.read_text() + "\nnamespace TileXR {\n" + poll + "\n}\n"
    (folder / "mte_state_generated.h").write_text(generated)
    header = header.replace("using namespace AscendC;", '#include "mte_state_generated.h"\nusing namespace AscendC;', 1)
    header = header.replace("    bool detailed;", "    bool detailed;\n    LocalTensor<uint32_t> stateRead, stateWrite;", 1)
    header = header.replace("TileXR::UDMAPollCQUntilSqTail(info, peer, qp, head, *this)",
                            "TileXR::SimulatorPollCQUntilSqTail(info, peer, qp, head, *this, stateRead, stateWrite)")
    start = header.index("struct LocalUrmaSend")
    before, after = header[:start], header[start:]
    before, flush_loads = state_loads(before, "stateRead")
    after, run_loads = state_loads(after, "queueRead")
    assert (flush_loads, run_loads) == (1, 2)
    for value, field in (("head", "headAddr"), ("completions", "wqeCntAddr")):
        old = f"st_dev({value}, reinterpret_cast<__gm__ uint32_t *>(sq->{field}), 0);"
        assert before.count(old) == 1
        before = before.replace(old, f"TileXR::SimulatorStore({value}, reinterpret_cast<__gm__ uint32_t *>(sq->{field}), stateWrite);")
    marker = "        auto *sq = TileXR::UDMAGetWQCtx(view.info, peer, view.gateway);"
    assert after.count(marker) == 1
    after = after.replace(marker, """        TBuf<QuePosition::VECCALC> queueState;
        pipe.InitBuffer(queueState, 128);
        auto queueRead = queueState.Get<uint32_t>();
        auto queueWrite = queueRead[16];
""" + marker)
    assert after.count("writer, detailed};") == 1
    after = after.replace("writer, detailed};", "writer, detailed, queueRead, queueWrite};")
    header = before + after
    if builder == "interleave":
        begin = header.index("// Reuse the EP UBX Gather/Mull/Scatter pattern.")
        end = header.index("#else\nconstexpr uint32_t kWqeWords", begin)
        header = header[:begin] + '#include "simd_wqe_interleave.h"\n' + header[end:]
        shutil.copyfile(WORK / "simd_wqe_interleave.h", folder / "simd_wqe_interleave.h")
    else:
        assert builder == "original"
    assert "TileXR::UDMARingDoorbell(head, sq);" in header
    assert "TileXR::UDMAPublishWqe(wqes, sq, head, count);" in header
    assert "ld_dev(" not in header
    (folder / "combine_ht_kernel.h").write_text(header)
    shutil.copyfile(original / "combine_ht.cpp", folder / "combine_ht.cpp")
    return {"poll_loads": poll_loads, "send_loads": flush_loads + run_loads, "state_stores": 4}


def main(args):
    builder = args.get("builder", "original")
    folder = WORK / ("mte-" + builder + "-" + str(args["attempt"]))
    folder.mkdir()
    result = {"ok": False, "baseline_commit": "1c9d7fed6188e1093efc456579130de3ca8be4e6",
              "variant": "mte_queue_state_" + builder + "_simd", "source_ranks": 64,
              "ranks": 8, "bs": 4, "topk": 16, "hidden": 256, "report": str(folder)}
    try:
        baseline = Path("/baseline")
        snapshot = json.loads((baseline / "environment.snapshot.json").read_text())
        sst.verify_environment(snapshot)
        revisions = json.loads((baseline / "revisions.json").read_text())
        assert revisions["candidate"] == result["baseline_commit"]
        result["baseline_object_sha256"] = sst.checksum(sst.KERNEL)
        result["changes"] = patch(folder, builder=builder)
        binary = folder / "send.o"
        with (folder / "build.log").open("wb") as log:
            sst.build_kernel(snapshot["settings"], log, source=folder / "combine_ht.cpp", binary=binary)
        result["object_sha256"] = sst.checksum(binary)
        result["build_completed"] = True
        specs, routes = sst.fixtures.prepare(8, 4, 256, 16, 4, 40, 64)
        prepared = [x.serialize() for x in specs]
        route_bytes = json.dumps(routes, separators=(",", ":")).encode()
        result["route_sha256"] = hashlib.sha256(route_bytes).hexdigest()
        result["send_rows"] = sum(len(x.gather_rows) for x in specs)
        sst.KERNEL = binary
        stages = []
        result["stages"] = stages
        state = sst.run_phase(folder / "send-only", 1, specs, prepared, snapshot,
                              {"ranks": 8, "threads": 16, "stage_timeout": 240}, stages)
        result.update(ok=True, send_verified=True, verified_ranks=len(state))
    except Exception as e:
        result.update(error=type(e).__name__, failure=str(e)[:300])
    sst.write_json(folder / "trial-result.json", result)
    sst.write_json(WORK / "trial-result.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "stages"}, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(json.loads(sys.argv[1])))
