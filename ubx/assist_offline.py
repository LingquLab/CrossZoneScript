"""Analyze named Windows Dispatch archives without exporting raw captures."""

from collections import Counter, defaultdict
import csv
import io
import json
import os
from pathlib import Path
import re
from statistics import median
import struct
from zipfile import ZipFile


def span(values):
    return [round(value, 3) for value in (min(values), median(values), max(values))] if values else None


def emit(section, value):
    print(json.dumps({section: value}, separators=(",", ":"), ensure_ascii=True), flush=True)


def load(archive, name):
    return json.loads(archive.read(name))


def role(core):
    return ("SEND" if core < 16 else "RECV" if core < 32 else "RELAY" if core < 48 else "INTRA")


def provenance(lines):
    return {key: value for line in lines if "=" in line
            for key, value in (line.split("=", 1),)
            if key in ("revision", "source_patch_sha256", "kernel_profiling", "packet_records")}


def compact_tail(row):
    return {
        "round": row["round"], "native_us": row["native_us"], "slow_rank": row["native_slowest_rank"],
        "copyout_role": row.get("copyout_rows_by_role", {}),
        "prefix_us": {k: round(v, 3) for k, v in row["slow_rank_prefix_us"].items()},
        "tail_cores": [row["tail"]["producer"], row["tail"]["consumer"]],
        "last_ready_us": round(row["last_ready"], 3),
        "flag_to_submit_us": row["tail"]["relay_flag_to_mem_submit_us"],
        "detect_to_load": round(row["tail"]["detected_to_load"], 3),
        "load_to_issue": round(row["tail"]["load_to_copyout_issue"], 3),
        "drain_before_probe": row["tail"]["drain_before_positive_probe"],
        "earlier_copies": row["tail"]["other_copyouts_after_producer_drain_before_detect"],
    }


def summarize_archive(archive, tag):
    measures = {}
    for name in archive.namelist():
        match = re.fullmatch(r"runs/(tail-(base|reviewed)-dispatch-bs(32|64|128)-(on|off))/timing.json", name)
        if not match:
            continue
        case = match[1]
        row = load(archive, name)
        measures[case] = {
            "native_us": row["native_task_us"]["round_slowest_rank"],
            "stream_us": row.get("round_slowest_rank"),
            "provenance": provenance(row["provenance"]),
            "max_tokens": row.get("effective_max_tokens", row.get("max_tokens")),
            "routes": row.get("route_sha256"),
            "profiles": {key: compact_tail(item) for key, item in row.get("packet_diagnostics", {}).items()},
        }
        if tag == "AB" and match[2] == "reviewed" and match[4] == "on":
            finish = {}
            for kind, detail in row["packet_diagnostics"].items():
                report = load(archive, f"runs/{case}/tail-report/{kind}-round-{detail['round']}.json")
                rank = detail["native_slowest_rank"]
                local = [route for route in report["routes"] if route["consumer"][0] == rank]
                all_recv = report["routes"]
                first_issue = {tuple(item["consumer"]): item for item in
                               sorted(local, key=lambda item: item["timestamps"]["flag_probe_issue_us"], reverse=True)}
                finish[kind] = {
                    "rank_first_probe": span([item["timestamps"]["flag_probe_issue_us"] for item in first_issue.values()]),
                    "rank_last_drain": round(max(item["recv_drain_us"] for item in local), 3),
                    "global_last_drain": round(max(item["recv_drain_us"] for item in all_recv), 3),
                    "global_recv_drains": span([max(item["recv_drain_us"] for item in all_recv
                                                    if item["consumer"][0] == peer) for peer in range(32)]),
                }
            measures[case]["reference_rounds"] = finish
    emit(tag + "_timing", measures)
    return measures


FILE = struct.Struct("<8sHHHHHBBI16sQ")
STREAM = struct.Struct("<IIIIQ")
RECORD = struct.Struct("<QII")
PAIRS = {"run": (1, 2), "send": (25, 26), "relay": (35, 36), "final_drain": (43, 44),
         "intra": (47, 48), "recv": (49, 50), "topk": (240, 241), "prefix": (270, 271),
         "dir_local": (290, 291), "dir_remote": (292, 293), "prefix_ready": (286, 287),
         "wait_cqe": (82, 83), "recv_control": (112, 113), "recv_probe": (114, 115),
         "recv_load": (116, 117), "copy_issue": (120, 121), "recv_wait": (122, 123),
         "ready_load": (414, 415), "ready_check": (416, 417)}
CUMULATIVE = {"wait_cqe", "recv_control", "recv_probe", "recv_load", "copy_issue", "recv_wait",
              "ready_load", "ready_check"}


def raw_metrics(data, rank, calibration):
    magic, major, minor, fh, item, sh, endian, reserved, lanes, capture, size = FILE.unpack_from(data)
    if (magic, major, minor, fh, item, sh, endian, reserved, lanes, size) != (
            b"TXRPRF1\0", 1, 0, 48, 16, 24, 1, 0, 168, len(data)):
        raise ValueError("unexpected SDK header")
    streams = {}
    at = FILE.size
    for _ in range(lanes):
        ident, count, dropped, flags, block = STREAM.unpack_from(data, at)
        core = ident & 0xffff
        if ident >> 16 != rank or core in streams or dropped or flags != 1 or block != 24 + 16 * count:
            raise ValueError("incomplete SDK stream")
        streams[core] = [(tick, event) for tick, event, _ in
                         RECORD.iter_unpack(data[at + 24:at + block])]
        at += block
    if at != size or streams.keys() != set(range(lanes)):
        raise ValueError("SDK stream count mismatch")
    if calibration["state"] != 2 or calibration["rank"] != rank:
        raise ValueError("invalid per-rank clock model")
    # Subtract the nearby integer target before adding the small affine offset.
    target, anchor = calibration["target"], calibration["anchor_cycle"]
    offset, drift = calibration["offset_cycles"], calibration["rate_correction"]
    frequency = calibration["cycles_per_second"] / 1e6

    def stamp(tick):
        return (tick - target + offset + drift * (tick - anchor)) / frequency

    cores = []
    for core in range(56):
        row = {"role": role(core), "core": core}
        markers = dict((event, tick) for tick, event in streams[core])
        cumulative = dict((event, tick) for tick, event in streams[core + 56])
        if 238 in markers or not (1 in markers and 2 in markers):
            raise ValueError("missing run or milestone overflow")
        for name, (start, end) in PAIRS.items():
            values = cumulative if name in CUMULATIVE else markers
            if start in values and end in values:
                row[name + "_dur"] = round((values[end] - values[start]) / frequency, 3)
                if name not in CUMULATIVE:
                    row[name + "_start"] = round(stamp(values[start]), 3)
                    row[name + "_end"] = round(stamp(values[end]), 3)
        packet = defaultdict(list)
        for tick, event in streams[core + 112]:
            kind = (event >> 25) & 63
            if not event & 0x80000000 or kind == 63:
                raise ValueError("packet trace incomplete")
            packet[kind].append((tick, event & 63))
        if 32 not in packet or 33 not in packet or len(packet[48]) != len(packet[49]):
            raise ValueError("packet coverage mismatch")
        row.update(
            probes=len(packet[48]), probed_rows=sum(amount for _, amount in packet[48]),
            empty_probes=sum(amount == 0 for _, amount in packet[49]), copyouts=len(packet[28]),
            first_probe=stamp(packet[48][0][0]) if packet[48] else None,
            last_ready=stamp(packet[22][-1][0]) if packet[22] else None,
            copy_done=stamp(packet[31][-1][0]) if packet[31] else None,
            cq_observed=stamp(packet[10][-1][0]) if packet[10] else None,
            role_done=stamp(markers[43]) if 43 in markers else None,
        )
        cores.append(row)
    return capture, target, cores


def archive_inputs(archives, batch):
    records, summaries, native = {}, {}, defaultdict(set)
    prefix = f"runs/tail-reviewed-dispatch-bs{batch}-on/"
    for archive in archives:
        for name in archive.namelist():
            if not name.startswith(prefix):
                continue
            match = re.search(r"/rank-(\d+)-round-(\d+)\.(records\.bin|summary\.json)$", name)
            if match:
                rank, step = map(int, match.groups()[:2])
                if match[3] == "records.bin":
                    records[step, rank] = archive, name
                else:
                    summaries[step, rank] = load(archive, name)
            elif "/op_summary" in name.lower() and name.lower().endswith(".csv"):
                match = re.search(r"/rank-(\d+)/", name)
                if match is None:
                    continue
                rank = int(match[1])
                with io.TextIOWrapper(archive.open(name), encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        row = {re.sub(r"[^a-z0-9]", "", key.lower()): value for key, value in row.items() if key}
                        if "tilexr_ep_ubx_kernel" in (row.get("opname"), row.get("optype")):
                            native[rank].add((float(row["taskstarttimeus"]), float(row["taskdurationus"])))
    ordered = {rank: [duration for _, duration in sorted(values)] for rank, values in native.items()}
    return records, summaries, ordered


def analyze_current(archives, batch, expected):
    records, summaries, native = archive_inputs(archives, batch)
    case = f"tail-reviewed-dispatch-bs{batch}-on"
    complete = [step for step in range(25) if all((step, rank) in records for rank in range(32))]
    correct = [step for step in complete if all(summaries.get((step, rank), {}).get("correctness") is True
                                               for rank in range(32))]
    per_rank = defaultdict(list)
    late_roles = Counter()
    examples = {}
    measured = []
    for step in complete:
        if step < 5:
            continue
        ids, targets = set(), set()
        ranks = {}
        for rank in range(32):
            archive, name = records[step, rank]
            sidecar = load(archive, name.removesuffix(".records.bin") + ".clock.json")
            capture, target, rows = raw_metrics(archive.read(name), rank, sidecar)
            ids.add(capture)
            targets.add(target)
            ranks[rank] = rows
            active = [row for row in rows if row["copyouts"]]
            per_rank["probe_batches"].append(sum(row["probes"] for row in active))
            per_rank["empty_probes"].append(sum(row["empty_probes"] for row in active))
            per_rank["probed_rows"].append(sum(row["probed_rows"] for row in active))
            per_rank["copyouts"].append(sum(row["copyouts"] for row in active))
            initial = sum((row["copyouts"] + 15) // 16 for row in active)
            per_rank["initial_batches"].append(initial)
            last = max(active, key=lambda row: row["copy_done"])
            late_roles[last["role"]] += 1
        if len(ids) != 1 or len(targets) != 1:
            raise ValueError("mixed rounds across ranks")
        native_complete = len(native) == 32 and all(len(rows) == 25 for rows in native.values())
        slow = max(ranks, key=lambda rank: native[rank][step] if native_complete else max(
                   row["run_dur"] for row in ranks[rank]))
        rows = ranks[slow]
        metric = native[slow][step] if native_complete else max(row["run_dur"] for row in rows)
        measured.append((metric, step))
        selected = next((tag for tag, row in expected.get(case, {}).get("profiles", {}).items()
                         if row["round"] == step), None)
        if selected or batch == 128:
            roles = {}
            for name in ("SEND", "RECV", "RELAY", "INTRA"):
                group = [row for row in rows if row["role"] == name]
                views = [row for row in group if row["copyouts"]]
                critical = max(views, key=lambda row: row["copy_done"], default=None)
                roles[name] = {
                    "run_end": round(max(row["run_end"] for row in group), 3),
                    "role_done": round(max(row["role_done"] for row in group), 3),
                    "first_probe": span([row["first_probe"] for row in views]),
                    "last_drain": round(critical["copy_done"], 3) if critical else None,
                    "tail_core": {k: round(critical[k], 3) for k in
                                  ("core", "copyouts", "probes", "empty_probes", "first_probe", "last_ready",
                                   "recv_dur", "dir_local_dur", "dir_remote_dur", "wait_cqe_dur",
                                   "recv_control_dur", "recv_probe_dur", "recv_load_dur", "copy_issue_dur", "recv_wait_dur")
                                  if critical and critical.get(k) is not None},
                    "cq_before_first_probe": sum(row["cq_observed"] is not None and
                                                 row["cq_observed"] <= row["first_probe"] for row in views),
                    "cq_during_recv": sum(row["cq_observed"] is not None and
                                          row["first_probe"] < row["cq_observed"] <= row["copy_done"] for row in views),
                    "cq_after_recv": sum(row["cq_observed"] is not None and
                                         row["cq_observed"] > row["copy_done"] for row in views),
                }
            examples[step] = {"round": step, "scope": selected, "rank": slow, "us": round(metric, 3), "roles": roles}
    if batch == 128 and examples:
        cutoff = sorted((value, step) for value, step in measured if step in correct)
        keep = [cutoff[len(cutoff) // 2][1], cutoff[-1][1]] if cutoff else []
        examples = {step: examples[step] for step in keep}
    emit(f"bs{batch}_raw", {
        "complete_capture_rounds": complete, "passing_summary_rounds": correct,
        "native_csv_ranks": {size: count for size, count in Counter(map(len, native.values())).items()},
        "configured_tokens": sorted({row["max_tokens"] for row in summaries.values() if "max_tokens" in row}),
        "native_or_kernel_run_us": span([value for value, step in measured if step in correct]),
        "native_available": bool(native) and len(native) == 32 and all(len(v) == 25 for v in native.values()),
        "per_rank_min_median_max": {key: span(values) for key, values in per_rank.items()},
        "last_copyout_rank_rounds": late_roles, "selected": list(examples.values()),
    })


def main():
    folder = Path(os.environ["USERPROFILE"]) / "Downloads"
    with ZipFile(folder / "TileXR-UBX-EP32-Dispatch-tail-AB.zip") as baseline, ZipFile(
        folder / "TileXR-e8ee4fff-profiling-on-184-2f281cf1.zip"
    ) as first, ZipFile(folder / "TileXR-e8ee4fff-profiling-on-84-2f281cf1.zip") as second:
        summarize_archive(baseline, "AB")
        current = summarize_archive(first, "e8")
        archives = (first, second)
        for batch in (64, 128):
            analyze_current(archives, batch, current)


if __name__ == "__main__":
    main()
