from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import time


REVISION = "2495e9dc9cc9571b6f09cf4ee3ab876cd373c115"
SOURCE_SHA = "8c8943ab9e0e1ad992123048183b25ab863a3ded7af246113315b6312d8c5eb9"
BASE_SOURCE_SHA = "fa10b46cbd4a3bd36b2f0b1e0afc191d6a7a6fd23905fc0b136c8bdd41acd9c2"
MAPPING_SHA = "0690e7435430c943b48713d1695d96547bf31ecd516e19b2db69428316db4ba7"
RECORDER_SHA = "c4d2f82bff3995ad56be9a8621bdd0df8c474183a764e2fc36d7152892345abd"
ROOT = Path("/opt/sim/simulator/Testcases/dispatch_64p_top6_late_wait_20261123_01")
RECEIPT = ROOT / "build-receipt.json"
BASE_CAMPAIGN = ROOT.with_name("dispatch_64p_top6_profile_ab_20261120_01")
ORDER = ("tl-off", "tl-on")
ENTRY = "tilexr_tl_dispatch_runtime_c64_profile"


def utc():
    return datetime.now(timezone.utc).isoformat()


def move_slot_wait(source):
    wait = re.compile(
        r"(?m)^ +if \(\(2 <= issued\).*\n"
        r" +AscendC::WaitFlag<AscendC::HardEvent::MTE3_S>\(\(slot \+ 2\)\);\n +}\n"
    )
    destination = re.compile(r"(?m)^ +record\.SetValue\(\(\(\(int64_t\)assist_word\) \+ \(int64_t\)2\), [^\n]+\n")
    (block,) = list(wait.finditer(source))
    (anchor,) = list(destination.finditer(source))
    if anchor.start() <= block.end():
        raise ValueError("unexpected baseline order for slot wait and source metadata")
    return source[:block.start()] + source[block.end():anchor.end()] + block[0] + source[anchor.end():]


def updated_mapping(source):
    mapping = json.loads(source)
    mapping.update(
        raw_sha256="b2184a076df46c81bc051a20c275144c936933f5e54a766c553240ab3e5b2943",
        device_sha256=SOURCE_SHA,
        source_export_sha256="34d2abc4cd74b547a86782ef3c337dac6045f435f6e27ea09a5c50da7826999f",
    )
    return (json.dumps(mapping, indent=2) + "\n").encode()


def prepare():
    started = time.monotonic()
    base_plan = json.loads((BASE_CAMPAIGN / "preflight.json").read_text())
    environment = base_plan["environment_sha256"]
    sha = PREP["sha"]
    for name, expected in environment.items():
        if sha(Path(name)) != expected:
            raise ValueError(f"qualified CANN/SST file changed: {name}")
    base_case, kernel_name, baseline_elf = PREP["SOURCES"]["tl"]
    base_build = PREP["TL_BUILD"]
    if sha(base_case / kernel_name) != baseline_elf or sha(base_build / "dispatch.o") != baseline_elf:
        raise ValueError("ub01 baseline ELF changed")
    if sha(base_build / "dispatch.profile.cpp") != BASE_SOURCE_SHA:
        raise ValueError("ub01 baseline generated source changed")
    candidate_source = move_slot_wait((base_build / "dispatch.profile.cpp").read_text()).encode()
    candidate_mapping = updated_mapping((base_build / "runtime-profile.json").read_bytes())
    manifest = json.loads((BASE_CAMPAIGN / "inputs-and-golden.json").read_text())
    shared_inputs = PREP["comparable_inputs"](base_case)
    golden = PREP["inventory"](base_case, "expected")
    if shared_inputs != manifest["shared_inputs"] or golden != manifest["golden"]:
        raise ValueError("input or golden diverged from the four-round baseline")
    config = PREP["fixed_config"](base_case)
    if config != base_plan["cases"]["tl-on"]["fixed_config_sha256"]:
        raise ValueError("fixed perf/NoC settings diverged from the four-round baseline")
    PREP["stage_case"].__globals__["ROOT"] = ROOT
    plan = {"candidate": REVISION, "status": "BUILDING", "order": ORDER, "cases": {},
            "started_utc": utc(), "environment_sha256": environment,
            "input_hash_count": len(shared_inputs), "golden_hash_count": len(golden)}
    PREP["write_json"](RECEIPT, plan)
    PREP["write_json"](ROOT / "inputs-and-golden.json", manifest)
    for label in ORDER:
        plan["current"] = label
        PREP["write_json"](RECEIPT, plan)
        build = ROOT / ("build-" + label)
        shutil.copytree(base_build, build, ignore=shutil.ignore_patterns(
            "dispatch.o", "dispatch.rel.o", "dispatch.elf.txt", "build.log",
            "build-receipt.json", "direct-compile.log",
        ))
        files = {
            "dispatch.profile.cpp": (candidate_source, SOURCE_SHA),
            "runtime-profile.json": (candidate_mapping, MAPPING_SHA),
        }
        for name, (data, expected) in files.items():
            path = build / name
            path.write_bytes(data)
            if sha(path) != expected:
                raise ValueError(f"transported candidate hash does not match: {name}")
        recorder = build / "include/tilexr/profiling/device.h"
        if sha(recorder) != RECORDER_SHA:
            raise ValueError("ub01 recorder hash does not match")
        mapping = json.loads(candidate_mapping)
        if (mapping["entry"], mapping["args_bytes_aarch64"], len(mapping["event_slots"]),
                mapping["device_sha256"]) != (ENTRY, 112, 30, SOURCE_SHA):
            raise ValueError("candidate ABI or 30-slot mapping does not match handoff")
        compiler = build / "compile.sh"
        cmd = compiler.read_text()
        flag = "-DTILEXR_ENABLE_PROFILING=1"
        if cmd.count(flag) != 1:
            raise ValueError("expected one ub01 profiling switch")
        enabled = int(label.endswith("-on"))
        compiler.write_text(cmd.replace(str(base_build), str(build)).replace(
            flag, f"-DTILEXR_ENABLE_PROFILING={enabled}"))
        compile_started = utc()
        compile_clock = time.monotonic()
        PREP["run_build"](["bash", str(compiler)], build, build / "compile-candidate.log")
        target = build / kernel_name
        identity = {
            "entry": ENTRY, "source_commit": REVISION, "elf_sha256": PREP["elf_identity"](target, ENTRY),
            "generated_source_sha256": SOURCE_SHA, "runtime_profile_sha256": MAPPING_SHA,
            "recorder_sha256": RECORDER_SHA, "profile_enabled": enabled,
            "compile_started_utc": compile_started, "compile_finished_utc": utc(),
            "compile_wall_seconds": round(time.monotonic() - compile_clock, 3),
            "compile_log": str(build / "compile-candidate.log"), "compile_rc": 0,
        }
        case = PREP["stage_case"](label, target, identity)
        directory = Path(case["directory"])
        metadata_path = directory / "tilelang-scenario.json"
        metadata = json.loads(metadata_path.read_text())
        metadata.update(revision=REVISION, build_path=str(build))
        PREP["write_json"](metadata_path, metadata)
        if case["fixed_config_sha256"] != config:
            raise ValueError(f"unexpected model or topology configuration: {label}")
        plan["cases"][label] = case
        PREP["write_json"](RECEIPT, plan)
    if len({case["elf_sha256"] for case in plan["cases"].values()}) != len(ORDER):
        raise ValueError("profiling switch produced identical ELFs")
    plan.pop("current")
    plan.update(status="READY", finished_utc=utc(),
                preparation_wall_seconds=round(time.monotonic() - started, 3))
    PREP["write_json"](RECEIPT, plan)
    PREP["write_json"](ROOT / "preflight.json", plan)
    return plan


def main():
    inflight = RUNNER["simulated_processes"]()
    if ROOT.exists():
        return {"status": "ALREADY_STAGED", "directory": str(ROOT),
                "build": json.loads(RECEIPT.read_text()) if RECEIPT.is_file() else None,
                "sst_processes": inflight}
    ROOT.mkdir(exist_ok=False)
    try:
        plan = prepare()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        state = json.loads(RECEIPT.read_text()) if RECEIPT.is_file() else {"candidate": REVISION}
        state.update(status="FAILED", error=str(error)[:620], finished_utc=utc())
        PREP["write_json"](RECEIPT, state)
        return {"status": "BUILD_FAILED", "build": state, "build_receipt": str(RECEIPT)}
    CAMPAIGN.update(ROOT=ROOT, ORDER=ORDER, PREFLIGHT=ROOT / "preflight.json",
                    STATUS=ROOT / "campaign-status.json")
    try:
        result = CAMPAIGN["run_campaign"]()
    except (OSError, ValueError, KeyError) as error:
        result = {"status": "FAILED", "stage": "preflight", "error": str(error)[:620]}
        if not CAMPAIGN["STATUS"].exists():
            PREP["write_json"](CAMPAIGN["STATUS"], result)
    result.update(candidate=REVISION, directory=str(ROOT), build=plan,
                  build_receipt=str(RECEIPT), sst_processes_before_preparation=inflight)
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=True))
