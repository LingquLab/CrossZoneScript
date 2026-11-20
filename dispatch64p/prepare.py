import hashlib
import json
import os
from collections import Counter
from pathlib import Path
import re
import shutil
import subprocess
import sys


BASE_BUILD = Path("/work/src/dispatch_tilelang_64p_68d32608_profile03")
BASE_CASE = Path("/opt/sim/simulator/Testcases/dispatch_tilelang_64p_top6_68d32608_profile03")
VARIANT = "ub01" if sys.argv[1:] == ["ub"] else "direct01"
BUILD = BASE_BUILD.with_name("dispatch_tilelang_64p_68d32608_" + VARIANT)
CASE = BASE_CASE.with_name("dispatch_tilelang_64p_top6_68d32608_" + VARIANT)
BASE_ELF_SHA = "9305032e21919730aa9d469b211a6e365a3eb9531c50e5a66c465aa111a85dee"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("unexpected source shape for " + before[:70])
    return text.replace(before, after, 1)


def recorder_candidate(original):
    text = replace_once(original, "TILEXR_PROFILE_DEVICE Recorder()", """TILEXR_PROFILE_DEVICE void Initialize(Detail::DeviceArgsAddress descriptor, uint32_t logicalStream)""")
    text = replace_once(text, """        for (uint32_t i = 0U; i < RecordCapacity; ++i) {
            eventIds_[i] = 0U;
        }
#endif""", """        enabled_ = lane_.Initialize(descriptor, logicalStream);
#else
        (void)descriptor;
        (void)logicalStream;
#endif""")
    text = replace_once(text, """        timestamps_[Slot] = static_cast<uint64_t>(AscendC::GetSystemCycle());
        eventIds_[Slot] = EventId;""", """        if (!enabled_) {
            return;
        }
        if (count_ == lane_.capacity) {
            dropped_ = Detail::SaturatingAdd(dropped_, 1U);
            return;
        }
        const uint64_t timestamp = static_cast<uint64_t>(AscendC::GetSystemCycle());
        lane_.timestamps[count_] = timestamp;
        lane_.eventIds[count_] = EventId;
        ++count_;""")
    text = replace_once(text, "void Flush(Detail::DeviceArgsAddress descriptor, uint32_t logicalStream)", "void Flush()")
    start = text.index("        Detail::LaneWriter lane;", text.index("void Flush()"))
    end = text.index("++count;", start) + len("++count;")
    end = text.index("}", end) + 1
    text = text[:start] + """        if (!enabled_) {
            return;
        }
        auto &lane = lane_;
        const uint32_t count = count_;
        const uint32_t dropped = dropped_;""" + text[end:]
    start = text.index("#else", text.index("lane.Commit", text.index("void Flush()")))
    end = text.index("#endif", start)
    disabled = text[start:end]
    if "(void)descriptor;" not in disabled or "(void)logicalStream;" not in disabled:
        raise ValueError("unexpected inactive Flush")
    text = text[:start] + text[end:]
    text = replace_once(text, """    uint64_t timestamps_[RecordCapacity];
    uint32_t eventIds_[RecordCapacity];""", """    Detail::LaneWriter lane_;
    uint32_t count_ = 0U;
    uint32_t dropped_ = 0U;
    bool enabled_ = false;""")
    if "DataCacheCleanAndInvalid" not in text or "lane.Commit(count, dropped)" not in text:
        raise ValueError("candidate must preserve the baseline export protocol")
    return text


def kernel_candidate(original):
    text, replaced = re.subn(
        r"(TileXR::Profiling::Recorder<\d+> recorder;)",
        r"\1\n  recorder.Initialize(profileGM, static_cast<uint32_t>(AscendC::GetBlockIdx()));",
        original,
    )
    if replaced != 1:
        raise ValueError("unexpected recorder declaration")
    text, replaced = re.subn(r"recorder\.Flush\([^;]+\);", "recorder.Flush();", text)
    if replaced != 1 or text.count("recorder.RecordMarker<") != 30:
        raise ValueError("unexpected marker or flush count")
    return text


def recorder_ub_candidate(original):
    text = replace_once(original, "TILEXR_PROFILE_DEVICE Recorder()", """TILEXR_PROFILE_DEVICE void Initialize(AscendC::LocalTensor<uint64_t> timestamps, AscendC::LocalTensor<uint32_t> eventIds)""")
    text = replace_once(text, """        for (uint32_t i = 0U; i < RecordCapacity; ++i) {
            eventIds_[i] = 0U;
        }
#endif""", """        timestamps_ = timestamps;
        eventIds_ = eventIds;
        for (uint32_t i = 0U; i < RecordCapacity; ++i) {
            eventIds_.SetValue(i, 0U);
        }
#else
        (void)timestamps;
        (void)eventIds;
#endif""")
    text = replace_once(text, """        timestamps_[Slot] = static_cast<uint64_t>(AscendC::GetSystemCycle());
        eventIds_[Slot] = EventId;""", """        const uint64_t timestamp = static_cast<uint64_t>(AscendC::GetSystemCycle());
        timestamps_.SetValue(Slot, timestamp);
        eventIds_.SetValue(Slot, EventId);""")
    text = replace_once(text, "if (eventIds_[slot] == 0U)", """const uint32_t eventId = eventIds_.GetValue(slot);
            if (eventId == 0U)""")
    text = replace_once(text, "lane.timestamps[count] = timestamps_[slot];", "lane.timestamps[count] = timestamps_.GetValue(slot);")
    text = replace_once(text, "lane.eventIds[count] = eventIds_[slot];", "lane.eventIds[count] = eventId;")
    return replace_once(text, """    uint64_t timestamps_[RecordCapacity];
    uint32_t eventIds_[RecordCapacity];""", """    AscendC::LocalTensor<uint64_t> timestamps_;
    AscendC::LocalTensor<uint32_t> eventIds_;""")


def kernel_ub_candidate(original):
    match = re.search(r"pipe\.InitBuffer\(ascend_ub, (\d+)\);", original)
    if match is None or original.count("recorder.RecordMarker<") != 30:
        raise ValueError("unexpected source buffer or marker layout")
    prior_size = int(match[1])
    timestamp_offset = (prior_size + 31) // 32 * 32
    event_id_offset = timestamp_offset + 32 * 8
    new_size = event_id_offset + 32 * 4
    if new_size > 192 * 1024:
        raise ValueError("profiling scratch exceeds the exported 192 KiB UB budget")
    text = replace_once(original, match[0], f"pipe.InitBuffer(ascend_ub, {new_size});")
    return replace_once(text, "TileXR::Profiling::Recorder<32> recorder;", f"""TileXR::Profiling::Recorder<32> recorder;
  recorder.Initialize(ascend_ub.GetWithOffset<uint64_t>(32, {timestamp_offset}),
                      ascend_ub.GetWithOffset<uint32_t>(32, {event_id_offset}));""")


def previous_timeout_inventory():
    directory = BASE_CASE.with_name("dispatch_tilelang_64p_top6_68d32608_direct01")
    receipt = json.loads((directory / "direct-run-receipt.json").read_text())
    if receipt.get("sst_rc") != 124:
        return {"prior_status": receipt.get("status")}
    counts = Counter()
    for path in (directory / "bin").glob("*.bin"):
        role = re.sub(r"^david_\d+_(?:die[12]_)?", "", path.stem)
        if role in {"diagnostics", "profile", "output", "assist"}:
            category = role if path.stat().st_size else role + "_empty"
            counts[category] += 1
    lines = (directory / "run.log").read_text(errors="replace").splitlines()
    shutdown = next((i for i, line in enumerate(lines) if "EMERGENCY SHUTDOWN" in line), len(lines))
    patterns = r"simulation is complete|deadlock|timeout|mismatch|warning|error|run start|simulation start"
    recent = [line.strip()[:160] for line in lines[max(0, shutdown - 90):shutdown]
              if re.search(patterns, line, re.I)]
    return {"prior_status": receipt["status"], "saved_bin_counts": dict(counts),
            "log_lines_before_shutdown": shutdown, "near_shutdown_signals": recent[-6:]}


def prepare_build():
    if digest(BASE_BUILD / "dispatch.o") != BASE_ELF_SHA:
        raise ValueError("wrong build baseline ELF")
    shutil.copytree(BASE_BUILD, BUILD, ignore=shutil.ignore_patterns(
        "dispatch.o", "dispatch.rel.o", "dispatch.elf.txt", "build.log", "build-receipt.json",
    ))
    recorder = BUILD / "include/tilexr/profiling/device.h"
    source = BUILD / "dispatch.profile.cpp"
    patch_recorder, patch_kernel = (
        (recorder_ub_candidate, kernel_ub_candidate) if VARIANT == "ub01"
        else (recorder_candidate, kernel_candidate)
    )
    recorder.write_text(patch_recorder(recorder.read_text()))
    source.write_text(patch_kernel(source.read_text()))
    compile_script = BUILD / "compile.sh"
    compile_script.write_text(compile_script.read_text().replace(str(BASE_BUILD), str(BUILD)))
    with (BUILD / "direct-compile.log").open("wb") as log:
        result = subprocess.run(
            ["bash", str(compile_script)], cwd=BUILD, stdout=log, stderr=subprocess.STDOUT,
            timeout=200,
        )
    if result.returncode:
        logs = (BUILD / "direct-compile.log").read_text(errors="replace")
        diagnostics = [line.strip()[:220] for line in logs.splitlines()
                       if "error:" in line.lower() or "not found" in line.lower()]
        return {"phase": "compile", "status": "FAILED", "exit": result.returncode,
                "diagnostics": diagnostics[:5]}
    elf = BUILD / "dispatch.o"
    description = subprocess.check_output(["readelf", "-h", "-sW", str(elf)], text=True, timeout=12)
    if not re.search(r"Type:\s+EXEC\b", description) or "0x990000" not in description:
        raise ValueError("candidate ELF must be C310 EXEC")
    original_mapping = json.loads((BUILD / "runtime-profile.json").read_text())
    if original_mapping["entry"] not in description:
        raise ValueError("candidate ELF missing baseline entry")
    return {"phase": "compile", "status": "READY", "elf_sha256": digest(elf),
            "profile_source_sha256": digest(source), "recorder_sha256": digest(recorder),
            "entry": original_mapping["entry"]}


def prepare_case(elf_sha):
    if digest(BASE_CASE / "dispatch.o") != BASE_ELF_SHA:
        raise ValueError("wrong case baseline ELF")
    CASE.mkdir(exist_ok=False)
    for name in ("input", "expected"):
        shutil.copytree(BASE_CASE / name, CASE / name, copy_function=os.link)
    for name in ("UBusSim_config", "v310_config"):
        shutil.copytree(BASE_CASE / name, CASE / name)
    for name in (
        "audited_launcher.py", "expected-v310-runtime.json", "output_generator.py",
        "test_UBusSim.py", "tilelang-scenario.json", "workload.yml", "workload_config.yml",
    ):
        shutil.copy2(BASE_CASE / name, CASE / name)
    shutil.copy2(BUILD / "dispatch.o", CASE / "dispatch.o")
    for path in CASE.rglob("*"):
        if (not path.is_file() or path.suffix not in (".yml", ".yaml", ".json", ".py") or
                path.relative_to(CASE).parts[0] in ("input", "expected")):
            continue
        original = path.read_text()
        rebased = original.replace(str(BASE_CASE), str(CASE)).replace(str(BASE_BUILD), str(BUILD))
        rebased = rebased.replace(BASE_ELF_SHA, elf_sha)
        if rebased != original:
            path.write_text(rebased)
    workloads = [CASE / name for name in (
        "workload.yml", "workload_config.yml", "v310_config/workload_config.yml",
    )]
    hashes = [digest(path) for path in workloads]
    if len(set(hashes)) != 1 or str(CASE) not in workloads[0].read_text():
        raise ValueError("candidate workload copies differ or were not rebased")
    if digest(CASE / "dispatch.o") != elf_sha:
        raise ValueError("case and compile object differ")
    return {"case": str(CASE), "workload_sha256": hashes[0],
            "input_files": sum(1 for path in (CASE / "input").rglob("*.bin")),
            "golden_files": sum(1 for path in (CASE / "expected").rglob("*.bin"))}


def main():
    try:
        if BUILD.exists() or CASE.exists():
            raise ValueError("candidate build or case already exists")
        result = prepare_build()
        if result["status"] == "READY":
            result.update(prepare_case(result["elf_sha256"]))
            if VARIANT == "ub01":
                result["direct01_timeout_evidence"] = previous_timeout_inventory()
            (CASE / "direct-preflight.json").write_text(json.dumps(result, indent=2) + "\n")
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        result = {"status": "FAILED", "error": str(error)[:260], "type": type(error).__name__}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
