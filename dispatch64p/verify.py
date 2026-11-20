from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
from tempfile import mkdtemp
import time


CASE = Path("/opt/sim/simulator/Testcases/dispatch_tilelang_64p_top6_68d32608_ub01"
            if sys.argv[1:] == ["ub"]
            else "/opt/sim/simulator/Testcases/dispatch_tilelang_64p_top6_68d32608_direct01")
BASELINE = CASE.with_name("dispatch_tilelang_64p_top6_68d32608_profile03")
ELF_SHA = ("8c5fcf1de1c333d3294aeb6475348e340cd88e8694d2f2e80dd25566ad2a9ee0"
           if sys.argv[1:] == ["ub"]
           else "6d3107745c9f0997abc107c1eab5f36cfc278ce1fd228a09a151137eb1e69a37")
RANKS = 64
CORES = 64
OUTPUT_NAMES = {"output", "expert_nums", "counts", "assist"}
CAPTURE_SIZE = 197120
PROFILE_OFFSETS = (512, 66048, 98816, 131584, 164352)
RANK_GM_STRIDE = 128 * 1024 ** 3
DESCRIPTOR = struct.Struct("<8I5Q")
EVENT_ORDER = (1, 10, 11, 100, 101, 200, 201, 300, 301, 400, 401, 500, 501,
               600, 601, 700, 704, 705, 706, 707, 708, 709, 701, 702, 703,
               800, 801, 900, 901, 2)
STAGES = {
    (1, 2): "module.whole", (10, 11): "F0.send_layout", (100, 101): "F1.route_read",
    (200, 201): "F2.expert_count", (300, 301): "F3.count_publish",
    (400, 401): "F4.pack_send", (500, 501): "F5.wait_count",
    (600, 601): "F6.prefix_publish", (700, 701): "F7.layout_wait",
    (704, 705): "F7.gate_poll", (706, 707): "F7.gate_clear",
    (708, 709): "F7.count_clear", (702, 703): "F7.receive_plan",
    (800, 801): "F8.payload_loop", (900, 901): "F9.payload_clear",
}


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, data):
    with path.open("x", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=True)
        output.write("\n")


def required_events(core):
    omitted = {400, 401} if core >= 56 else {200, 201, 300, 301, 500, 501, 600, 601}
    if core != 0:
        omitted.update({708, 709})
    return tuple(event for event in EVENT_ORDER if event not in omitted)


def raw_blocks(path):
    blob = path.read_bytes()
    if len(blob) % 520:
        raise ValueError("invalid die dump record size for " + path.name)
    blocks = {}
    for offset in range(0, len(blob), 520):
        address = struct.unpack_from("<Q", blob, offset)[0]
        value = blob[offset + 8:offset + 520]
        if address in blocks and blocks[address] != value:
            raise ValueError("conflicting blocks in " + path.name)
        blocks[address] = value
    return blocks


def read_die_buffers(rank, name, size):
    blocks = [raw_blocks(CASE / "bin" / f"david_{rank}_die{die}_{name}.bin") for die in (1, 2)]
    addresses = set(blocks[0]) | set(blocks[1])
    if len(addresses) != (size + 511) // 512:
        raise ValueError(f"rank {rank} {name} dump coverage mismatch")
    base = min(addresses)
    if addresses != set(range(base, base + size, 512)):
        raise ValueError(f"rank {rank} {name} dump is not contiguous")
    buffers = [b"".join(die.get(address, bytes(512))
                        for address in range(base, base + size, 512))[:size] for die in blocks]
    return base, buffers


def read_field(buffers, fmt, offset):
    left, right = (struct.unpack_from(fmt, blob, offset)[0] for blob in buffers)
    if left and right and left != right:
        raise ValueError(f"conflicting nonzero values across dies at offset {offset}")
    return left or right


def capture_layout(rank, base, buffers):
    fields = tuple(read_field(buffers, "<I", 4 * index) for index in range(8))
    abi, descriptor_bytes, enabled, streams, capacity, record_stride, state_stride, reserved = fields
    if ((abi, descriptor_bytes, enabled, streams, reserved) != (1, DESCRIPTOR.size, 1, CORES, 0) or
            capacity < len(EVENT_ORDER) or record_stride != 128 or state_stride != 128):
        raise ValueError(f"unexpected profiling DeviceView {fields}")
    system_base = base + rank * RANK_GM_STRIDE
    offsets = tuple(read_field(buffers, "<Q", 32 + 8 * index) - system_base for index in range(5))
    if offsets != PROFILE_OFFSETS:
        raise ValueError(f"rank {rank} profiling DeviceView has offsets {offsets}")
    return capacity, record_stride, state_stride


def lane_events(buffers, layout, core):
    capacity, record_stride, state_stride = layout
    times, ids, counts, dropped, states = PROFILE_OFFSETS
    count, lost, completion = (read_field(buffers, "<I", offset + 4 * state_stride * core)
                               for offset in (counts, dropped, states))
    expected = required_events(core)
    if completion != 2 or lost or count != len(expected) or count > capacity:
        raise ValueError(f"count {count}/{len(expected)} dropped {lost} completion {completion}")
    markers = tuple(read_field(buffers, "<I", ids + 4 * (record_stride * core + index))
                    for index in range(count))
    if markers != expected:
        raise ValueError(f"event sequence differs, missing {sorted(set(expected) - set(markers))}")
    ticks = tuple(read_field(buffers, "<Q", times + 8 * (record_stride * core + index))
                  for index in range(count))
    if not ticks[0] or any(left > right for left, right in zip(ticks, ticks[1:])):
        raise ValueError("zero start or nonmonotonic raw ticks")
    return dict(zip(markers, ticks))


def profile():
    totals = Counter()
    failures = []
    lanes = []
    for rank in range(RANKS):
        try:
            base, buffers = read_die_buffers(rank, "profile", CAPTURE_SIZE)
            layout = capture_layout(rank, base, buffers)
        except (ValueError, OSError, struct.error) as error:
            failures.extend([rank, core, str(error)] for core in range(CORES))
            continue
        for core in range(CORES):
            try:
                values = lane_events(buffers, layout, core)
            except ValueError as error:
                failures.append([rank, core, str(error)])
                continue
            intervals = {name: [values[start], values[end]] for (start, end), name in STAGES.items()
                         if start in values}
            totals.update(intervals.keys())
            lanes.append({"rank": rank, "core": core, "intervals": intervals})
    return lanes, dict(totals), failures


def golden():
    files = {}
    for path in (CASE / "expected").rglob("*.bin"):
        ranks = [int(match[1]) for part in path.relative_to(CASE / "expected").parts[:-1]
                 if (match := re.fullmatch(r"rank_?(\d+)", part))]
        name = path.stem.removeprefix("expected_")
        if len(ranks) != 1 or name not in OUTPUT_NAMES or (ranks[0], name) in files:
            raise ValueError("unexpected golden layout for " + path.name)
        files[ranks[0], name] = path
    if set(files) != {(rank, name) for rank in range(RANKS) for name in OUTPUT_NAMES}:
        raise ValueError("golden inventory does not cover four outputs per rank")
    findings = []
    for (rank, name), expected in sorted(files.items()):
        actual = CASE / "bin" / f"david_{rank}_{name}.bin"
        observed = sha(actual) if actual.exists() else None
        reference = sha(expected)
        findings.append({"rank": rank, "name": name, "actual_sha256": observed,
                         "golden_sha256": reference, "pass": observed == reference})
    return findings


def diagnostics():
    findings = []
    for rank in range(RANKS):
        try:
            _, buffers = read_die_buffers(rank, "diagnostics", CORES * 64)
            owned = []
            for core in range(CORES):
                records = {blob[64 * core:64 * (core + 1)] for blob in buffers}
                records -= {bytes(64), b"\xa5" * 64}
                if len(records) != 1:
                    raise ValueError(f"core {core} diagnostic owner mismatch")
                owned.append(struct.unpack("<16i", records.pop()))
        except (ValueError, OSError, struct.error) as error:
            findings.append({"rank": rank, "sent": None, "received": None, "errors": None,
                             "pass": False, "read_error": str(error)})
            continue
        sent = sum(row[5] for row in owned)
        received = sum(row[7] for row in owned)
        errors = sum(row[0] != 0 for row in owned)
        findings.append({"rank": rank, "sent": sent, "received": received, "errors": errors,
                         "pass": sent == received == 768 and errors == 0})
    return findings


def business_coverage(directory, receipt):
    audit_path = CASE / "config-load.jsonl"
    loads = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    model_loads = [entry for entry in loads if "chip_num" in entry.get("architecture", {})]
    chip_counts = sorted({entry["architecture"]["chip_num"] for entry in model_loads})
    outputs = golden()
    work = diagnostics()
    coverage = {}
    for name in sorted(OUTPUT_NAMES):
        present = [row["rank"] for row in outputs
                   if row["name"] == name and row["actual_sha256"] is not None]
        passed = [row["rank"] for row in outputs if row["name"] == name and row["pass"]]
        coverage[name] = {"rank_ids": present, "rank_count": len(present),
                          "golden_pass_rank_ids": passed, "golden_pass_count": len(passed)}
    result = {
        "status": "PASS" if chip_counts == [64] and all(row["pass"] for row in outputs + work)
        else "FAIL",
        "loaded_chip_num": chip_counts[0] if len(chip_counts) == 1 else None,
        "observed_chip_counts": chip_counts,
        "output_coverage": coverage, "golden_checked": len(outputs),
        "golden_passed": sum(row["pass"] for row in outputs),
        "golden_failure_examples": [row for row in outputs if not row["pass"]][:5],
        "ranks_with_valid_work": sum(row["pass"] for row in work),
        "total_sent": sum(row["sent"] or 0 for row in work),
        "total_received": sum(row["received"] or 0 for row in work),
        "per_rank_diagnostics": work,
    }
    write_json(directory / "output-sha.json", outputs)
    write_json(directory / "rank-work.json", work)
    write_json(directory / "business-coverage.json", {
        **result, "case": str(CASE), "elf_sha256": receipt["elf_sha256"],
        "workload_sha256": receipt["workload_sha256"],
        "config_load_audit_sha256": receipt["config_load_audit_sha256"],
        "model_loads": model_loads,
    })
    return result


def write_perfetto(directory, lanes):
    trace = []
    for lane in lanes:
        rank, core, intervals = lane["rank"], lane["core"], lane["intervals"]
        trace.append({"name": "thread_name", "ph": "M", "pid": rank, "tid": core + 1,
                      "args": {"name": f"AIV {core:02}"}})
        origin = intervals["module.whole"][0]
        for name, (start, end) in intervals.items():
            trace.append({"name": name, "cat": name.split(".")[0], "ph": "X",
                          "pid": rank, "tid": core + 1, "ts": (start - origin) / 1000,
                          "dur": (end - start) / 1000,
                          "args": {"raw_begin_tick": start, "raw_end_tick": end,
                                   "raw_duration_tick": end - start}})
    artifact = directory / "dispatch.raw-ticks.perfetto.json"
    write_json(artifact, {"traceEvents": trace, "displayTimeUnit": "ns",
                          "metadata": {"clock_alignment": "NOT_VERIFIED",
                                       "axis": "raw_ticks_visualized_at_one_tick_per_display_ns",
                                       "origin": "per_core_independent"}})
    return sha(artifact)


def wait_for_output():
    path = CASE / "direct-run-receipt.json"
    deadline = time.monotonic() + 2510
    while time.monotonic() < deadline:
        if path.is_file():
            receipt = json.loads(path.read_text())
            if receipt["status"] == "OUTPUT_READY":
                return receipt
            if receipt["status"] == "FAILED":
                raise ValueError("SST or readback failed in " + receipt["phase"])
        time.sleep(5)
    raise TimeoutError("candidate did not reach OUTPUT_READY within the observation window")


def verify():
    receipt = wait_for_output()
    if sha(CASE / "dispatch.o") != ELF_SHA or receipt["elf_sha256"] != ELF_SHA:
        raise ValueError("run and profile evidence have different ELF identities")
    if sha(CASE / "config-load.jsonl") != receipt["config_load_audit_sha256"]:
        raise ValueError("actual config-load audit changed")
    directory = Path(mkdtemp(prefix="analysis-direct-", dir=CASE))
    business = business_coverage(directory, receipt)
    lanes, totals, failures = profile()
    write_json(directory / "valid-stage-pairs.json", lanes)
    write_json(directory / "profile-failures.json", failures)
    trace_sha = write_perfetto(directory, lanes)
    complete = len(lanes) == RANKS * CORES and not failures
    result = {
        "status": "PASS" if complete and business["status"] == "PASS" else "FAIL",
        "run": receipt, "analysis_directory": str(directory),
        "validated_utc": datetime.now(timezone.utc).isoformat(),
        "business": business, "business_coverage_path": str(directory / "business-coverage.json"),
        "profile_lanes": len(lanes), "profile_failures": len(failures),
        "profile_failure_examples": failures[:7], "valid_pair_counts": totals,
        "expected_work_per_rank": 768,
        "trace_sha256": trace_sha, "clock_alignment": "NOT_VERIFIED",
        "baseline_config_audit_sha256": sha(BASELINE / "config-load.jsonl"),
    }
    write_json(directory / "summary.json", result)
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(verify()))
    except (OSError, ValueError, KeyError, struct.error) as error:
        print(json.dumps({"status": "FAILED", "phase": "verification",
                          "error": str(error)[:300]}))
