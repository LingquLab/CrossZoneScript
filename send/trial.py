# Copyright (c) 2026 TileXR Project.
"""Build an MTE queue-state variant and validate the existing 8P BS4 Send fixture.

The baseline tree is mounted read-only. This script writes generated source,
objects and reports only under /work. The selected builder either retains the
original Scatter path or uses the separately recorded contiguous SIMD stores.
"""
import hashlib
from contextlib import redirect_stdout
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
from packed_builder import transform as pack_builder
from packed_builder import VF_BUILDERS, transform_vf as pack_builder_in_vf
from backward_alignment import repack as repack_backward, write_common_header
from simd_probe_variants import install as install_simd_probe
from store_controls import KINDS as STORE_KINDS, decode as decode_store_status
from builder_controls import KINDS as MASK_BUILDERS, transform as local_mask_builder

STORE_BUILDERS = tuple("scalar_" + kind for kind in STORE_KINDS)

sys.path.insert(0, "/baseline")
import sst

WORK = Path("/work")


def state_loads(text, scratch):
    pattern = r"ld_dev\((reinterpret_cast<__gm__ uint32_t\s*\*>\([^)]+\)),\s*0\)"
    return re.subn(pattern, r"TileXR::SimulatorLoad(\1, " + scratch + ")", text)


def patch(folder, baseline=Path("/baseline"), builder="original", force_inline=False, mte_status=False, backward_record_bytes=24, reference_compat=False):
    write_common_header(folder, baseline, backward_record_bytes)
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
    helper_text = helper.read_text()
    if reference_compat:
        assert helper_text.count("gm.SetGlobalBuffer(address, 16U);") == 1
        helper_text = helper_text.replace("gm.SetGlobalBuffer(address, 16U);",
            "gm.SetGlobalBuffer(address, 1U);\n    gm.SetL2CacheHint(AscendC::CacheMode::CACHE_MODE_DISABLE);")
        assert helper_text.count("DataCopyExtParams{1U, 64U,") == 1
        helper_text = helper_text.replace("DataCopyExtParams{1U, 64U,", "DataCopyExtParams{1U, 4U,")
    generated = helper_text + "\nnamespace TileXR {\n" + poll + "\n}\n"
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
    if reference_compat:
        begin = header.index("inline void NativeLoad(")
        end = header.index("inline void NativeStore(", begin)
        body = header[begin:end]
        assert body.count("gm.SetGlobalBuffer(source);") == 1
        body = body.replace("gm.SetGlobalBuffer(source);", "gm.SetGlobalBuffer(source);\n    gm.SetL2CacheHint(AscendC::CacheMode::CACHE_MODE_DISABLE);")
        header = header[:begin] + body + header[end:]
    if builder in ("interleave", "interleave_packed", "scalar_control", "scalar_simd_probe", "scalar_simd_gather", "scalar_simd_interleave") + STORE_BUILDERS + MASK_BUILDERS + VF_BUILDERS:
        begin = header.index("// Reuse the EP UBX Gather/Mull/Scatter pattern.")
        end = header.index("#else\nconstexpr uint32_t kWqeWords", begin)
        builder_name = "scalar_wqe_control.h" if builder.startswith("scalar_") else "simd_wqe_interleave.h"
        header = header[:begin] + '#include "' + builder_name + '"\n' + header[end:]
        builder_text = (WORK / builder_name).read_text()
        if builder in MASK_BUILDERS:
            builder_text = local_mask_builder(builder_text, builder)
        if builder == "interleave_packed":
            header, builder_text = pack_builder(header, builder_text)
        if builder in VF_BUILDERS:
            header, builder_text = pack_builder_in_vf(header, builder_text)
        if force_inline:
            assert builder == "interleave"
            marker = "__aicore__ inline void BuildPutWqes("
            assert builder_text.count(marker) == 1
            builder_text = builder_text.replace(marker, "__attribute__((always_inline)) inline __aicore__ void BuildPutWqes(")
        (folder / builder_name).write_text(builder_text)
        if builder in ("scalar_simd_gather", "scalar_simd_interleave") + STORE_BUILDERS:
            kind = builder[len("scalar_"):] if builder in STORE_BUILDERS else (
                "gather" if builder == "scalar_simd_gather" else "interleave")
            header, source = install_simd_probe(header, (WORK / "simd_wqe_interleave.h").read_text(), kind)
            (folder / "simd_operation_probe.h").write_text(source)
        if builder == "scalar_simd_probe":
            helper = "minimal_simd_probe.h"
            header = header.replace('#include "' + builder_name + '"', '#include "' + builder_name + '"\n#include "' + helper + '"')
            shutil.copyfile(WORK / helper, folder / helper)
            begin = header.index("struct LocalPutBatch")
            end = header.index("struct LocalUrmaSend", begin)
            body = header[begin:end]
            marker = "        SetFlag<HardEvent::V_MTE3>(EVENT_ID0);"
            assert body.count(marker) == 1
            probe = """        TileXR::UDMASyncEvent<AscendC::HardEvent::S_V>();
        ProbeMinimalSimd(reinterpret_cast<__ubuf__ uint32_t *>(stateRead.GetPhyAddr()));
        TileXR::UDMASyncEvent<AscendC::HardEvent::V_S>();
        for (uint32_t word = 0U; word < 16U; ++word) {
            if (stateRead.GetValue(word) != kMinimalSimdProbeValue) return TileXR::TILEXR_UDMA_STATUS_INVALID;
        }
"""
            header = header[:begin] + body.replace(marker, probe + marker) + header[end:]
    else:
        assert builder == "original" and not force_inline
    assert "TileXR::UDMARingDoorbell(head, sq);" in header
    assert "TileXR::UDMAPublishWqe(wqes, sq, head, count);" in header
    assert "ld_dev(" not in header
    if mte_status:
        marker = "        reinterpret_cast<__gm__ uint32_t *>(args.status)[core] = LocalUrmaSend::Run(view, writer, args.profiling != nullptr);"
        assert header.count(marker) == 1
        replacement = """        const uint32_t sendStatus = LocalUrmaSend::Run(view, writer, args.profiling != nullptr);
        TPipe statusPipe;
        TBuf<QuePosition::VECCALC> statusBuffer;
        statusPipe.InitBuffer(statusBuffer, kUbAlign);
        auto statusUb = statusBuffer.Get<uint32_t>();
        statusUb.SetValue(0U, sendStatus == 0U ? (0xa5000000U | (args.config.rank << 8U) | core) : sendStatus);
        NativeStore(reinterpret_cast<__gm__ uint32_t *>(args.status) + core, statusUb, 1U);"""
        if reference_compat:
            replacement = replacement.replace("+ core, statusUb", "+ core * 8U, statusUb")
        header = header.replace(marker, replacement)
    (folder / "combine_ht_kernel.h").write_text(header)
    shutil.copyfile(original / "combine_ht.cpp", folder / "combine_ht.cpp")
    return {"poll_loads": poll_loads, "send_loads": flush_loads + run_loads, "state_stores": 4,
            "force_inline": force_inline, "mte_status": mte_status,
            "packed_builder": builder == "interleave_packed" or builder in VF_BUILDERS,
            "decode_inside_vf": builder in VF_BUILDERS, "minimal_simd_probe": builder == "scalar_simd_probe",
            "backward_record_bytes": backward_record_bytes,
            "reference_compat": reference_compat, "state_read_bytes": 4 if reference_compat else 64,
            "status_slot_bytes": 32 if reference_compat else 4, "native_load_disables_l2": reference_compat,
            "simd_operation_probe": builder if builder in ("scalar_simd_gather", "scalar_simd_interleave") else "none"}


def stop_at_boundary(header, boundary):
    if boundary == "none":
        return header
    begin = header.index("struct LocalPutBatch")
    end = header.index("struct LocalUrmaSend", begin)
    body = header[begin:end]
    if boundary == "before_builder":
        marker = "        uint32_t row_begin = 0;"
        replacement = "        return TileXR::TILEXR_UDMA_STATUS_SUCCESS; // Diagnostic: stop before builder.\n" + marker
    else:
        assert boundary == "before_sq_publish"
        marker = "        TileXR::UDMAPublishWqe(wqes, sq, head, count);"
        replacement = """        TileXR::UDMASyncEvent<AscendC::HardEvent::MTE3_S>();
        return TileXR::TILEXR_UDMA_STATUS_SUCCESS; // Diagnostic: snapshot complete, SQ untouched.
""" + marker
    assert body.count(marker) == 1
    return header[:begin] + body.replace(marker, replacement) + header[end:]


def status_image(slot_bytes):
    assert slot_bytes in (4, 32)
    image = bytearray(512)
    for core in range(8):
        struct.pack_into("<I", image, core * slot_bytes, 0x5a5a5a5a)
    return bytes(image)


def status_output(values, slot_bytes):
    assert slot_bytes in (4, 32) and len(values) == 512
    words = tuple(struct.unpack_from("<I", values, core * slot_bytes)[0] for core in range(8))
    guards = sum(values[core * slot_bytes + 4:(core + 1) * slot_bytes] != bytes(slot_bytes - 4)
                 for core in range(8))
    guards += values[8 * slot_bytes:] != bytes(512 - 8 * slot_bytes)
    return words, guards


def capture(folder, specs, prepared, snapshot, boundary="none", mte_status=False, backward_record_bytes=24, probe_kind=None, status_slot_bytes=4):
    run = folder / "capture"
    workload = sst.create_workload(run, specs, prepared, 1)
    for rank, spec in enumerate(specs):
        chip = workload["workload_config"][f"chip{rank}"]
        assert chip["output_param"]["status"]["size"] == 512
        if status_slot_bytes == 32:
            assert chip["kernel_param"]["status"]["value"] % 32 == 0
        (run / "input_bin" / f"rank{rank}" / "status.bin").write_bytes(status_image(status_slot_bytes))
        if backward_record_bytes == 32:
            assert chip["kernel_param"]["backward"]["value"] % 32 == 0
            assert len(prepared[rank][0]["backward"]) % 32 == 0
        address = rank * sst.rt.GM_BYTES + 0x280000000
        size = 8 * (spec.backward_capacity + spec.chunks) * 64
        image = run / "input_bin" / f"rank{rank}" / "snapshot.bin"
        image.write_bytes(b"\xcd" * size)
        chip["kernel_param"]["wqe_snapshot"]["value"] = address
        chip["kernel_param"]["load_snapshot"] = dict(value=address, param_offset="512", param_type="11", input_bin=str(image))
        chip["output_param"]["wqe_snapshot"] = dict(addr=address, size=size)
    with (folder / "model.log").open("w") as log, redirect_stdout(log):
        sst.write_model(run, snapshot, workload, 8)
    env = snapshot["settings"]
    started = time.monotonic()
    with (run / "sim.log").open("wb") as log:
        p = subprocess.run(sst.shell_command(env, "sst", [env["sst_bin"], "-n", "16", run / "test_UBusSim.py"]),
                           cwd=run, stdout=log, stderr=subprocess.STDOUT, timeout=240)
    out = dict(diagnostic_only=True, returncode=p.returncode, wall_s=time.monotonic() - started,
               wqe_verified=False, checked_wqes=0, mismatch_count=0, first_mismatch=[], status_words=[],
               checked_wqe_fields=["source", "destination", "length"], stop_boundary=boundary,
               checked_status_words=0, nonzero_status_words=0, status_mismatch_count=0, untouched_snapshots=0,
               status_guard_failures=0,
               status_encoding="rank_core_marker" if mte_status else "plain_status")
    if probe_kind:
        out.update(probe_kind=probe_kind, probe_mismatch_count=0, probe_first_mismatches=[])
    if p.returncode:
        file = run / "sim.log"
        with file.open("rb") as log:
            log.seek(max(0, file.stat().st_size - 32768))
            tail = log.read().decode(errors="replace")
        fatal = next((line for line in tail.splitlines() if "FATAL:" in line), "")
        address = re.search(r"\[addr=\s*(\d+)\]", fatal)
        out["fatal"] = {"unknown_scalar_memory": "LsuL1: Unknown low level mem" in fatal,
                        "address": int(address[1]) if address else None}
        return out
    mismatches = []
    for rank, spec in enumerate(specs):
        chip = workload["workload_config"][f"chip{rank}"]
        output = chip["output_param"]["wqe_snapshot"]
        raw = sst.read_output(run, rank, "wqe_snapshot", output["addr"], output["size"])
        status = chip["output_param"]["status"]
        values = sst.read_output(run, rank, "status", status["addr"], status["size"])
        status_words, status_guards = status_output(values, status_slot_bytes)
        if probe_kind:
            for core, status in enumerate(status_words):
                decoded = decode_store_status(status, probe_kind)
                if decoded:
                    out["probe_mismatch_count"] += 1
                    if len(out["probe_first_mismatches"]) < 3:
                        out["probe_first_mismatches"].append(dict(rank=rank, core=core, **decoded))
        out["checked_status_words"] += len(status_words)
        out["nonzero_status_words"] += sum(value != 0 for value in status_words)
        expected_status = tuple((0xa5000000 | (rank << 8) | core) if mte_status else 0 for core in range(8))
        out["status_mismatch_count"] += sum(actual != expected for actual, expected in zip(status_words, expected_status))
        out["status_guard_failures"] += status_guards
        out["untouched_snapshots"] += raw == b"\xcd" * len(raw)
        if rank == 0:
            out["status_words"] = list(status_words)
        if boundary == "before_builder":
            continue
        own_input = chip["kernel_param"]["input"]["value"]
        for gateway, stages in enumerate(spec.backward):
            if gateway == rank:
                continue
            peer = workload["workload_config"][f"chip{gateway}"]
            remote = peer["kernel_param"]["workspace"]["value"]
            ordinal = 0
            for stage, records in enumerate(stages):
                expected = []
                for _, source_row, packed_row in records:
                    expected.append((ordinal, own_input + source_row * 512,
                                     remote + spec.gather_offset + (packed_row & 0xffffffff) * 512, 512))
                    ordinal += 1
                if records:
                    expected.append((spec.backward_capacity + stage,
                        chip["kernel_param"]["workspace"]["value"] + (3 * 64 + gateway) * 512,
                        remote + (3 * 64 + 96 + stage * 8 + rank) * 512, 8))
                for row, source, destination, length in expected:
                    offset = (gateway * (spec.backward_capacity + spec.chunks) + row) * 64
                    words = struct.unpack_from("<16I", raw, offset)
                    actual = (words[14] | words[15] << 32, words[10] | words[11] << 32, words[12])
                    out["checked_wqes"] += 1
                    if actual != (source, destination, length):
                        mismatches.append(dict(rank=rank, gateway=gateway, stage=stage, row=row,
                                               expected=[source, destination, length], actual=actual))
    sst.write_json(folder / "mismatches.json", mismatches)
    out.update(wqe_verified=not mismatches and out["checked_wqes"] > 0 and
               out["status_mismatch_count"] == 0 and out["status_guard_failures"] == 0,
               mismatch_count=len(mismatches), first_mismatch=mismatches[:1])
    if boundary == "before_builder":
        out["boundary_verified"] = (out["checked_status_words"] == 64 and
            out["status_mismatch_count"] == 0 and out["status_guard_failures"] == 0 and out["untouched_snapshots"] == 8)
    return out


def verify_builder_interface(folder, builder, env, result):
    from vf_codegen import inspect as inspect_generated_ir
    result["builder_ir"] = inspect_generated_ir(folder, env, sst)
    check = result["builder_ir"]
    assert (check.get("compile_returncode") == check.get("decode_returncode") == 0
            and check.get("identified") and check.get("generic_pointer_parameter_count") == 0), "predicate_vf_interface_check_failed"
    if builder in VF_BUILDERS:
        assert check["function"]["parameter_count"] == 1, "packed_vf_did_not_keep_one_parameter"


def main(args):
    builder = args.get("builder", "original")
    folder = WORK / ("mte-" + builder + "-" + str(args["attempt"]))
    folder.mkdir()
    result = {"ok": False, "baseline_commit": "1c9d7fed6188e1093efc456579130de3ca8be4e6",
              "variant": "mte_queue_state_" + builder, "source_ranks": 64,
              "ranks": 8, "bs": 4, "topk": 16, "hidden": 256, "report": str(folder),
              "force_inline": args.get("force_inline", False), "stop_boundary": args.get("stop_boundary", "none"),
              "mte_status": args.get("mte_status", False), "backward_record_bytes": args.get("backward_record_bytes", 24),
              "reference_compat": args.get("reference_compat", False)}
    result["vf_stack_off"] = args.get("vf_stack_off", False)
    try:
        baseline = Path("/baseline")
        snapshot = json.loads((baseline / "environment.snapshot.json").read_text())
        sst.verify_environment(snapshot)
        revisions = json.loads((baseline / "revisions.json").read_text())
        assert revisions["candidate"] == result["baseline_commit"]
        result["baseline_object_sha256"] = sst.checksum(sst.KERNEL)
        assert result["baseline_object_sha256"] == "c95c61586a5f9e97c83eb862e83b3cfde3ac9a22dec0b6a3332c67434b7ac85e"
        result["changes"] = patch(folder, builder=builder, force_inline=result["force_inline"], mte_status=result["mte_status"],
                                  backward_record_bytes=result["backward_record_bytes"], reference_compat=result["reference_compat"])
        if args.get("capture"):
            path = folder / "combine_ht_kernel.h"
            header = path.read_text()
            header = header.replace("TileXR::UDMARingDoorbell(head, sq);", "(void)sq; // No network submission in this capture.")
            header = header.replace("return TileXR::SimulatorPollCQUntilSqTail(info, peer, qp, head, *this, stateRead, stateWrite);",
                                    "return TileXR::TILEXR_UDMA_STATUS_SUCCESS; // No CQ wait in this capture.")
            header = stop_at_boundary(header, result["stop_boundary"])
            path.write_text(header)
        binary = folder / "send.o"
        with (folder / "build.log").open("wb") as log:
            if builder == "interleave_vf_packed_default":
                from vf_stack_control import build_default, spill_detail
                result["previous_spill"] = spill_detail(WORK / "mte-interleave_vf_packed-32")
                result["vf_stack_control"] = build_default(folder, snapshot["settings"], log,
                    preflight=lambda: verify_builder_interface(folder, builder, snapshot["settings"], result))
            elif result["vf_stack_off"]:
                from vf_stack_control import build as build_vf_stack_control
                preflight = (lambda: verify_builder_interface(folder, builder, snapshot["settings"], result)) if builder in VF_BUILDERS else None
                result["vf_stack_control"] = build_vf_stack_control(folder, builder, snapshot["settings"], log, preflight=preflight)
            else:
                sst.build_kernel(snapshot["settings"], log, source=folder / "combine_ht.cpp", binary=binary)
        if result["reference_compat"]:
            raw = binary.read_bytes()
            assert 0 < len(raw) <= 10485760
            result.update(raw_object_bytes=len(raw), raw_object_sha256=hashlib.sha256(raw).hexdigest())
            binary.write_bytes(raw.ljust(10485760, b"\0"))
        result["object_bytes"] = binary.stat().st_size
        result["object_sha256"] = sst.checksum(binary)
        result["build_completed"] = True
        if builder == "interleave_predicate_runtime":
            verify_builder_interface(folder, builder, snapshot["settings"], result)
        symbols = subprocess.run(["readelf", "-sW", str(binary)], check=True, capture_output=True,
                                 text=True, timeout=15).stdout
        result["outlined_builders"] = [line.split()[-1] for line in symbols.splitlines()
            if " FUNC " in line and "BuildPutWqes" in line and line.split()[-1].endswith(".vector.thread")]
        result["simd_probe_functions"] = [line.split()[-1] for line in symbols.splitlines()
            if " FUNC " in line and ("ProbeMinimalSimd" in line or "ProbeOperation" in line)]
        specs, routes = sst.fixtures.prepare(8, 4, 256, 16, 4, 40, 64)
        prepared, result["backward_preparation"] = repack_backward([x.serialize() for x in specs], result["backward_record_bytes"])
        route_bytes = json.dumps(routes, separators=(",", ":")).encode()
        result["route_sha256"] = hashlib.sha256(route_bytes).hexdigest()
        result["send_rows"] = sum(len(x.gather_rows) for x in specs)
        assert result["route_sha256"] == "f5eb786c6ca0a725607b16caef0ac8a8c0efc161576b185d9c74ffa2f4127881"
        assert result["send_rows"] == 260
        sst.KERNEL = binary
        if args.get("capture"):
            probe_kind = builder[len("scalar_"):] if builder in STORE_BUILDERS else None
            result.update(capture(folder, specs, prepared, snapshot, result["stop_boundary"], result["mte_status"], result["backward_record_bytes"], probe_kind,
                                  32 if result["reference_compat"] else 4))
            result["ok"] = result.get("boundary_verified", result["wqe_verified"])
            return save(folder, result)
        stages = []
        result["stages"] = stages
        state = sst.run_phase(folder / "send-only", 1, specs, prepared, snapshot,
                              {"ranks": 8, "threads": 16, "stage_timeout": 240}, stages)
        result.update(ok=True, send_verified=True, verified_ranks=len(state))
    except Exception as e:
        result.update(error=type(e).__name__, failure=str(e)[:300])
    return save(folder, result)


def save(folder, result):
    sst.write_json(folder / "trial-result.json", result)
    sst.write_json(WORK / "trial-result.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "stages"}, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(json.loads(sys.argv[1])))
