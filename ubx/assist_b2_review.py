"""Attribute completed B2DEA4E8 Dispatch measurements from the three local ZIPs."""

from collections import Counter, defaultdict
import os
from pathlib import Path
import re
from statistics import median
from zipfile import ZipFile

from assist_offline import PAIRS, archive_inputs, emit, load, provenance, raw_metrics, span


def tail_fields(row):
    return {key: row[key] for key in (
        "route", "producer", "consumer", "detected_us", "recv_drain_us", "drain_to_positive_probe_issue",
        "drain_before_positive_probe", "other_copyouts_after_producer_drain_before_detect",
        "detected_to_load", "load_to_copyout_issue", "copyout_issue_to_drain",
        "relay_flag_to_mem_submit_us",
    ) if key in row}


def read_report(archive):
    cases = {}
    patch_names = [name for name in archive.namelist() if name.endswith("fix_route_vector_counter.patch")]
    observed = []
    for name in patch_names:
        lines = archive.read(name).decode().splitlines()
        removed = [line[1:].strip() for line in lines if line.startswith("-") and not line.startswith("---")]
        added = [line[1:].strip() for line in lines if line.startswith("+") and not line.startswith("+++")]
        normalized = [line.replace("uint32_t", "uint16_t", 1) for line in removed]
        observed.append({"removed_lines": len(removed), "added_lines": len(added),
                         "only_u32_to_u16": len(removed) == 2 and normalized == added})
    for name in archive.namelist():
        match = re.fullmatch(r"(runs/[^/]*bs(32|64|128)[^/]*[-_](on|off))/timing\.json", name)
        if match is None:
            continue
        timing = load(archive, name)
        data = timing.get("native_task_us", {})
        per_round = timing.get("native_task_round_slowest_rank_us") or data.get("per_round", {})
        case = {
            "folder": match[1],
            "native_us": data.get("round_slowest_rank"),
            "native_per_round": per_round,
            "routes": timing.get("route_sha256"),
            "provenance": provenance(timing.get("provenance", [])),
            "trace": {label: {
                "round": row["round"], "rank": row.get("native_slowest_rank"), "tail": tail_fields(row["tail"]),
                "last_ready": row.get("last_ready"), "prefix": row.get("slow_rank_prefix_us"),
            } for label, row in timing.get("packet_diagnostics", {}).items()},
        }
        if match[3] == "off":
            case["off_rank_profiles"] = sum(item.startswith(match[1] + "/") and "/op_summary" in item
                                            for item in archive.namelist())
        cases[int(match[2]), match[3]] = case
    emit("report", {"patches": observed, "cases": {f"{bs}-{mode}": row for (bs, mode), row in cases.items()},
                    "other_report_entries": [entry for entry in archive.namelist()
                                             if entry.count("/") < 2][:20]})
    return cases


def done(rows, key):
    return max((entry[key] for entry in rows if entry.get(key) is not None), default=None)


def analyze(archives, bs):
    prefixes = {name.split("/rank-", 1)[0] + "/" for archive in archives for name in archive.namelist()
                if re.match(rf"runs/[^/]*bs{bs}[^/]*[-_]on/rank-\d+/.*\.records\.bin$", name)}
    if len(prefixes) != 1:
        emit(f"bs{bs}_unavailable", {"prefix_candidates": sorted(prefixes)})
        return
    prefix = prefixes.pop()
    records, summaries, native = archive_inputs(archives, bs, prefix)
    chosen = {}
    expected = {(step, rank) for step in range(25) for rank in range(32)}
    if not expected <= records.keys() or not expected <= summaries.keys():
        raise ValueError(f"BS{bs} incomplete rank captures")
    if not all(summaries[key].get("correctness") is True for key in expected):
        raise ValueError(f"BS{bs} failed rank correctness")
    if set(native) != set(range(32)) or any(len(rows) != 25 for rows in native.values()):
        raise ValueError(f"BS{bs} incomplete native samples")
    measured = sorted((max((native[rank][step], rank) for rank in range(32)), step) for step in range(5, 25))
    keep = {measured[i][1] for i in (9, 10, 19)}
    rank_rows, latest, late_roles = defaultdict(list), Counter(), Counter()
    for (_, slow_rank), step in measured:
        ids, targets = set(), set()
        for rank in range(32):
            archive, name = records[step, rank]
            clock = load(archive, name.removesuffix(".records.bin") + ".clock.json")
            identity, target, rows = raw_metrics(archive.read(name), rank, clock)
            ids.add(identity)
            targets.add(target)
            groups = {role: [row for row in rows if row["role"] == role] for role in ("SEND", "RECV", "RELAY", "INTRA")}
            drains = {role: done(group, "copy_done") for role, group in groups.items()}
            winner = max((time, role) for role, time in drains.items() if time is not None)[1]
            latest[winner] += 1
            for role, group in groups.items():
                for key in ("role_done", "copy_done", "mem_done", "prefix_total_end", "relay_compute_end",
                            "recv_index_end", "send_schedule_end"):
                    value = done(group, key)
                    if value is not None:
                        rank_rows[role + "_" + key].append(value)
                if role in ("SEND", "RECV"):
                    active = [row for row in group if row["first_probe"] is not None]
                    for key in ("probes", "empty_probes", "probed_rows", "copyouts"):
                        rank_rows[role + "_" + key].append(sum(row[key] for row in active))
                    if active:
                        rank_rows[role + "_first_probe"].append(median(row["first_probe"] for row in active))
                if role in ("RELAY", "INTRA"):
                    forwarded = [row["forwarded"] for row in group]
                    rank_rows[role + "_forwarded_imbalance"].append(max(forwarded) - min(forwarded))
            if rank != slow_rank:
                continue
            slowest_role = max(rows, key=lambda row: row["role_done"])["role"]
            late_roles[slowest_role] += 1
            if step not in keep:
                continue
            roles = {}
            for role, group in groups.items():
                representative = max(group, key=lambda row: row["role_done"])
                last = max((row for row in group if row["copy_done"] is not None),
                           key=lambda row: row["copy_done"], default=None)
                roles[role] = {"done": representative["role_done"], "core": representative["core"],
                               "mem_done": done(group, "mem_done"),
                               "forwarded_rows": span([row["forwarded"] for row in group]),
                               "first_probe": span([row["first_probe"] for row in group if row["first_probe"] is not None]),
                               "last_copyout": {key: last[key] for key in (
                                   "core", "copy_done", "last_ready", "first_probe", "probes", "empty_probes",
                                   "copyouts", "recv_probe_dur", "recv_load_dur", "copy_issue_dur", "recv_wait_dur",
                               ) if key in last} if last else None}
            chosen[step] = {"round": step, "rank": rank, "native": native[rank][step], "roles": roles,
                            "send_schedule_end": done(groups["SEND"], "send_schedule_end"),
                            "prefix_end": done(groups["RECV"], "prefix_total_end"),
                            "latest_core_end": done(rows, "run_end")}
        if len(ids) != 1 or len(targets) != 1:
            raise ValueError(f"BS{bs} mixed rank captures at round {step}")
    emit(f"bs{bs}_raw", {
        "correct_rank_rounds": len(expected), "max_tokens": sorted({row["max_tokens"] for row in summaries.values()
                                                                   if "max_tokens" in row}),
        "native_us": span([pair[0][0] for pair in measured]), "latest_copyout_roles": latest,
        "last_role_on_slowest_rank": late_roles,
        "rank_round_medians": {key: round(median(values), 3) for key, values in rank_rows.items()},
        "representative_rounds": [chosen[measured[i][1]] for i in (9, 10, 19)],
    })


def main():
    folder = Path(os.environ["USERPROFILE"]) / "Downloads"
    report = "TileXR-UBX-EP32-Dispatch-assist-b2dea4e8.zip"
    first = "TileXR-b2dea4e8-profiling-on-184-complete-f3f4de52.zip"
    second = "TileXR-b2dea4e8-profiling-on-84-complete-f3f4de52.zip"
    with ZipFile(folder / report) as package, ZipFile(folder / first) as a, ZipFile(folder / second) as b:
        PAIRS.update({"send_schedule": (29, 30), "prefix_total": (13, 14),
                      "relay_compute": (33, 34), "recv_index": (45, 46)})
        read_report(package)
        for batch in (32, 64, 128):
            analyze((a, b), batch)


if __name__ == "__main__":
    main()
