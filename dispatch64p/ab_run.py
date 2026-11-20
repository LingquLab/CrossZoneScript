from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import time


ROOT = Path("/opt/sim/simulator/Testcases/dispatch_64p_top6_profile_ab_20261120_01")
ORDER = ("native-off", "tl-off", "tl-on", "native-on")
NATIVE_TOOLS = ROOT / "build-native"
PREFLIGHT = ROOT / "preflight.json"
STATUS = ROOT / "campaign-status.json"
UNITS_US = {"ps": 1e-6, "ns": 1e-3, "us": 1, "ms": 1000, "s": 1e6}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_preflight(plan):
    if plan["status"] != "READY" or tuple(plan["order"]) != ORDER:
        raise ValueError("unexpected campaign preflight")
    for name, sha256 in plan["environment_sha256"].items():
        if VERIFY["sha"](Path(name)) != sha256:
            raise ValueError(f"runtime identity changed: {name}")
    for label in ORDER:
        spec = plan["cases"][label]
        case = Path(spec["directory"])
        if VERIFY["sha"](case / spec["kernel_name"]) != spec["elf_sha256"]:
            raise ValueError(f"ELF does not match preflight: {label}")
        for name in ("workload.yml", "workload_config.yml", "v310_config/workload_config.yml"):
            if VERIFY["sha"](case / name) != spec["workload_sha256"]:
                raise ValueError(f"workload changed: {label}/{name}")
        for name, sha256 in spec["fixed_config_sha256"].items():
            if VERIFY["sha"](case / name) != sha256:
                raise ValueError(f"model config changed: {label}/{name}")
        if any((case / name).exists() for name in ("run.log", "analysis-ab", "bin")):
            raise ValueError(f"run directory already contains evidence: {label}")


def atomic(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n")
    temp.replace(path)


def wait_idle(timeout):
    deadline = time.monotonic() + timeout
    while RUNNER["simulated_processes"]():
        if time.monotonic() >= deadline:
            raise TimeoutError("SST remains occupied")
        time.sleep(5)


def collect_actual_config(case, spec):
    audit = case / "config-load.jsonl"
    rows = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    loaded = {}
    chip_counts = set()
    for row in rows:
        arch = row.get("architecture", {})
        if "chip_num" in arch:
            chip_counts.add(arch["chip_num"])
        for name, (file, sha256) in row.get("files", {}).items():
            relative = str(Path(file).relative_to(case))
            amounts = loaded.setdefault(relative, set())
            amounts.add(sha256)
    expected = {name: {value} for name, value in spec["fixed_config_sha256"].items()}
    expected["v310_config/workload_config.yml"] = {spec["workload_sha256"]}
    if chip_counts != {64} or loaded != expected:
        raise ValueError(f"actual model does not match 64P fixed config, chips={sorted(chip_counts)}")
    return {"loaded_chip_num": 64, "load_audit_sha256": VERIFY["sha"](audit),
            "loaded_configs": {name: next(iter(values)) for name, values in loaded.items()}}


def decode_rank_work(assist, receiver, issued):
    if len(assist) != 768 * 12:
        raise ValueError(f"rank {receiver} expected 768 assist records, got {len(assist) // 12}")
    seen = set()
    for source, token, slot in struct.iter_unpack("<iii", assist):
        triple = (source, token, slot)
        if not (0 <= source < 64 and 0 <= token < 128 and 0 <= slot < 6) or triple in seen:
            raise ValueError(f"rank {receiver} contains invalid or duplicate assist record")
        seen.add(triple)
        if triple in issued["routes"]:
            raise ValueError("cross-rank duplicate route")
        issued["routes"].add(triple)
        issued["sent"][source] += 1
    return len(seen)


def validate_business(case, analysis, implementation):
    normalized = analysis / "actual"
    issued = {"routes": set(), "sent": Counter()}
    received = {}
    outputs = []
    for rank in range(64):
        folder = normalized / f"rank{rank}"
        if implementation == "native":
            folder.mkdir(parents=True)
        for name in sorted(VERIFY["OUTPUT_NAMES"]):
            expected = case / "expected" / f"rank{rank}" / (name + ".bin")
            actual = case / "bin" / f"david_{rank}_{name}.bin"
            size = expected.stat().st_size
            with actual.open("rb") as stream:
                data = stream.read(size) if implementation == "native" else stream.read()
            actual_sha = digest(data)
            expected_sha = VERIFY["sha"](expected)
            outputs.append({"rank": rank, "output": name, "size": len(data),
                            "sha256": actual_sha, "golden_sha256": expected_sha,
                            "pass": actual_sha == expected_sha})
            if actual_sha == expected_sha and name == "assist":
                received[rank] = decode_rank_work(data, rank, issued)
            if implementation == "native":
                (folder / (name + ".bin")).write_bytes(data)
    atomic(analysis / "output-sha.json", outputs)
    failed = [(row["rank"], row["output"]) for row in outputs if not row["pass"]]
    rank_work = [{"rank": rank, "sent": issued["sent"][rank], "received": received.get(rank, 0)}
                 for rank in range(64)]
    atomic(analysis / "rank-work.json", rank_work)
    if failed or any(row["sent"] != 768 or row["received"] != 768 for row in rank_work):
        raise ValueError(f"business output or rank work failed: {failed[:8]}")
    if implementation == "tl":
        work = VERIFY["diagnostics"]()
        atomic(analysis / "rank-diagnostics.json", work)
        if len(work) != 64 or any(not row["pass"] for row in work):
            raise ValueError("TileLang raw per-rank diagnostics failed")
    return {"rank_count": 64, "golden_checked": 256, "golden_passed": 256,
            "sent": len(issued["routes"]), "received": sum(received.values()),
            "business_evidence": str(analysis)}


def tilelang_profile(analysis):
    lanes, pairs, failures = VERIFY["profile"]()
    atomic(analysis / "valid-stage-pairs.json", lanes)
    atomic(analysis / "profile-failures.json", failures)
    if len(lanes) != 4096 or failures:
        raise ValueError(f"TileLang profile missing or invalid lanes: {failures[:6]}")
    stats = {}
    for lane in lanes:
        for name, (begin, end) in lane["intervals"].items():
            stats.setdefault(name, []).append((end - begin, lane["rank"], lane["core"]))
    modules = {
        "M1": ("F0.send_layout", "F1.route_read"),
        "M2": ("F2.expert_count", "F3.count_publish"),
        "M3": ("F4.pack_send",),
        "M4": ("F5.wait_count", "F6.prefix_publish"),
        "M5": ("F7.layout_wait", "F7.receive_plan", "F8.payload_loop", "F9.payload_clear"),
    }
    for module, names in modules.items():
        values = []
        for lane in lanes:
            intervals = sorted(lane["intervals"][name]
                               for name in names if name in lane["intervals"])
            if not intervals:
                continue
            if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
                raise ValueError(f"overlapping {module} intervals in one TileLang lane")
            values.append((sum(end-begin for begin, end in intervals), lane["rank"], lane["core"]))
        stats[module] = values
    report = {}
    for name, reads in stats.items():
        longest = max(ticks for ticks, _, _ in reads)
        report[name] = {"active_cores": len(reads), "mean_ticks": sum(row[0] for row in reads) / len(reads),
                        "max_ticks": longest,
                        "slowest": [[rank, core] for ticks, rank, core in reads if ticks == longest]}
    atomic(analysis / "stage-stats.json", report)
    trace = VERIFY["write_perfetto"](analysis, lanes)
    return {"profile_lanes": len(lanes), "profile_errors": 0, "lost_events": 0, "pairs": pairs,
            "trace_sha256": trace, "clock_alignment": "NOT_VERIFIED",
            "module_stats": {name: report[name] for name in modules},
            "stage_stats_path": str(analysis / "stage-stats.json")}


def native_profile(case, analysis):
    manifest = json.loads((case / "manifest.json").read_text())
    records = analysis / "records"
    for layout in manifest["rank_layouts"]:
        rank = layout["rank"]
        target = records / f"rank{rank}"
        target.mkdir(parents=True)
        for name, region in layout["profile_outputs"].items():
            merged = case / "bin" / f"david_{rank}_{name}.bin"
            size = region["readback_bytes"]
            with merged.open("rb") as stream:
                data = stream.read(size)
            if len(data) != size:
                raise ValueError(f"native profile size {rank}/{name}")
            (target / (name + ".bin")).write_bytes(data)
    report = analysis / "report"
    with (analysis / "native-report.log").open("wb") as log:
        proc = subprocess.run([
            sys.executable, str(NATIVE_TOOLS / "profile/report.py"), "--testcase", str(case),
            "--records", str(records), "--actual", str(analysis / "actual"),
            "--run-log", str(case / "run.log"), "--out", str(report),
        ], cwd=case, stdout=log, stderr=subprocess.STDOUT, timeout=90)
    if proc.returncode:
        raise ValueError(f"native profile parser failed, see {analysis / 'native-report.log'}")
    data = json.loads((report / "summary.json").read_text())
    if data["rank_count"] != 64 or data["lanes"] != 4096:
        raise ValueError("native profile did not validate all 4,096 lanes")
    stages = report / "stages-by-rank.csv"
    return {"profile_lanes": 4096, "profile_errors": 0, "lost_events": 0,
            "trace_sha256": VERIFY["sha"](report / "dispatch.raw-ticks.perfetto.json"),
            "clock_alignment": data["clock"]["clock_alignment"], "module_stats": data["modules"],
            "stage_stats_path": str(stages)}


def run_one(label, spec, status):
    case = Path(spec["directory"])
    implementation = label.split("-")[0]
    RUNNER["logged"].__globals__["CASE"] = case
    RUNNER["completion_values"].__globals__["CASE"] = case
    VERIFY["sha"].__globals__["CASE"] = case
    state = {"label": label, "case": str(case), "started_utc": RUNNER["utc"](),
             "stage": "sst", "elf_sha256": spec["elf_sha256"],
             "workload_sha256": spec["workload_sha256"]}
    status["current"] = state
    atomic(STATUS, status)
    bash = ["bash", "-c", RUNNER["COMMAND"], "dispatch-sst", RUNNER["AIV_AFFINITY"]]
    rc, wall = RUNNER["logged"](bash, "run.log", 1377)
    completion, errors = RUNNER["completion_values"]()
    state.update(sst_rc=rc, sst_wall_seconds=wall, completion=completion,
                 sst_finished_utc=RUNNER["utc"](), key_errors=errors)
    atomic(STATUS, status)
    if rc or len(completion) != 1 or not float(completion[0][0]) or errors:
        raise ValueError(f"{label} SST did not finish successfully")
    amount, unit = completion[0]
    state["simulated_us"] = float(amount) * UNITS_US[unit]
    state.update(collect_actual_config(case, spec))
    state["stage"] = "readback"
    atomic(STATUS, status)
    rc, wall = RUNNER["logged"](["python3", "-B", "output_generator.py", "v310_config"],
                                "output-generator.log", 100)
    state.update(output_generator_rc=rc, output_generator_wall_seconds=wall)
    if rc:
        raise ValueError(f"{label} output generation failed")
    state["stage"] = "validation"
    atomic(STATUS, status)
    analysis = case / "analysis-ab"
    analysis.mkdir()
    started = time.monotonic()
    state.update(validate_business(case, analysis, implementation))
    if label == "tl-on":
        state.update(tilelang_profile(analysis))
    elif label == "native-on":
        state.update(native_profile(case, analysis))
    state.update(status="PASS", stage="complete", validated_utc=RUNNER["utc"](),
                 validation_wall_seconds=round(time.monotonic() - started, 3))
    atomic(analysis / "summary.json", state)
    return state


def run_campaign():
    if STATUS.exists():
        raise ValueError("campaign already has a status receipt")
    plan = json.loads(PREFLIGHT.read_text())
    verify_preflight(plan)
    status = {"status": "WAITING", "preflight": str(PREFLIGHT), "created_utc": RUNNER["utc"](),
              "workers": 40, "cpu_affinity": RUNNER["AIV_AFFINITY"], "rounds": []}
    atomic(STATUS, status)
    try:
        wait_idle(1100)
        status.update(status="RUNNING", started_utc=RUNNER["utc"]())
        for label in ORDER:
            wait_idle(300)
            finished = run_one(label, plan["cases"][label], status)
            status["rounds"].append(finished)
            status.pop("current")
            atomic(STATUS, status)
        status["status"] = "PASS"
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        status.update(status="FAILED", error=str(error)[:620])
    status["finished_utc"] = RUNNER["utc"]()
    status["remaining_sst_pids"] = RUNNER["simulated_processes"]()
    atomic(STATUS, status)
    return status


if __name__ == "__main__":
    try:
        print(json.dumps(run_campaign(), ensure_ascii=True))
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "FAILED", "stage": "preflight", "error": str(error)[:620]}))
