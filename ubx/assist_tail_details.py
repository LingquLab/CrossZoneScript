"""Inspect critical INTRA cores and the actual coverage of each archive attempt."""

from collections import Counter, defaultdict
import os
from pathlib import Path
import re
from zipfile import ZipFile

from assist_offline import emit, load, raw_metrics, span


def core_details(archives, case, tag, packet):
    step = packet["round"]
    report = load(archives[0], f"{case}/tail-report/{tag}-round-{step}.json")
    tail = report["last_copyout_on_slowest_rank"]
    rank, core = tail["consumer"]
    folder = f"{case}/rank-{rank:02}/rank-{rank}-round-{step}"
    archive = next(archive for archive in archives if folder + ".records.bin" in archive.namelist())
    calibration = load(archive, folder + ".clock.json")
    native = raw_metrics(archive.read(folder + ".records.bin"), rank, calibration)[2]
    peers = [row for row in native if row["role"] == "INTRA"]
    local = [route for route in report["routes"] if route["consumer"] == tail["consumer"]]
    earlier = sum(tail["detected_us"] <= route["copyout_issued_us"] < tail["timestamps"]["recv_load_begin_us"]
                  for route in local)
    picked = native[core]
    fields = ("forwarded", "intra_start", "intra_end", "mem_first", "mem_last", "mem_done", "first_probe",
              "copy_done", "recv_dur", "copyouts")
    emit("bs64_critical_core", {
        "round": step, "role": tag, "critical": [rank, core],
        "tail_route": tail["route"], "earlier_copies_after_ready": earlier,
        "batch_ready_size": sum(route["detected_us"] == tail["detected_us"] for route in local),
        "core_trace": {key: round(picked[key], 3) for key in fields if picked.get(key) is not None},
        "all_intra_on_rank": [[row["core"], row["forwarded"], row.get("intra_end"),
                               round(row["first_probe"], 3) if row["first_probe"] is not None else None,
                               round(row["copy_done"], 3) if row["copy_done"] is not None else None] for row in peers],
        "core_send_end_to_first_probe": round(picked["first_probe"] - picked["intra_end"], 3),
        "core_max_clock_bound_us": calibration["clock_model_bound_ns"] / 1000,
    })


def attempt_coverage(archives):
    groups = defaultdict(lambda: {"records": defaultdict(set), "correct": defaultdict(set),
                                  "profiles": Counter(), "fail": [], "rank_samples": Counter()})
    prefix = r"((?:runs/|attempts/[0-9a-z-]+/)tail-reviewed-dispatch-bs(?:32|128)-on)/"
    for archive in archives:
        for name in archive.namelist():
            match = re.search(prefix + r"rank-(\d+)/rank-(\d+)-round-(\d+)\.(records\.bin|summary\.json)$",
                              name)
            if match:
                folder, dirname, rank, step, kind = match.groups()
                if int(dirname) != int(rank):
                    raise ValueError("inconsistent rank directory")
                row = groups[folder]
                rank, step = int(rank), int(step)
                if kind == "records.bin":
                    row["records"][step].add(rank)
                    row["rank_samples"][rank] += 1
                elif load(archive, name).get("correctness") is True:
                    row["correct"][step].add(rank)
                continue
            match = re.match(prefix, name)
            if match is None:
                continue
            row = groups[match[1]]
            if name.endswith("/failure.json"):
                detail = load(archive, name).get("reason", "")
                codes = re.search(r"\[[0-9A-Za-z ,]+\]", detail)
                row["fail"].append(codes[0] if codes else "other recorded failure")
            elif name.lower().endswith(".csv") and "/op_summary" in name.lower():
                row["profiles"]["native_summary"] += 1
    emit("attempts", {
        folder: {"records_by_round": {step: len(ranks) for step, ranks in sorted(row["records"].items())},
                 "correct_by_round": {step: len(ranks) for step, ranks in sorted(row["correct"].items())},
                 "native_csvs": row["profiles"]["native_summary"], "node_failure": row["fail"],
                 "rank_records": span(list(row["rank_samples"].values()))}
        for folder, row in groups.items()
    })


def main():
    folder = Path(os.environ["USERPROFILE"]) / "Downloads"
    with ZipFile(folder / "TileXR-UBX-EP32-Dispatch-tail-AB.zip") as baseline, ZipFile(
        folder / "TileXR-e8ee4fff-profiling-on-184-2f281cf1.zip"
    ) as first, ZipFile(folder / "TileXR-e8ee4fff-profiling-on-84-2f281cf1.zip") as second:
        archives = first, second
        case = "runs/tail-reviewed-dispatch-bs64-on"
        timing = load(first, f"{case}/timing.json")
        for tag, packet in timing["packet_diagnostics"].items():
            core_details(archives, case, tag, packet)
        baseline_timing = load(baseline, f"{case}/timing.json")
        comparable = {}
        for tag, packet in baseline_timing["packet_diagnostics"].items():
            tail = load(baseline, f"{case}/tail-report/{tag}-round-{packet['round']}.json")
            on_rank = [row for row in tail["routes"] if row["consumer"][0] == 16]
            comparable[tag] = {
                "rank16_last_drain": round(max(row["recv_drain_us"] for row in on_rank), 3),
                "rank16_first_probe": round(min(row["timestamps"]["flag_probe_issue_us"] for row in on_rank), 3),
            }
        emit("bs64_reviewed_rank16", comparable)
        attempt_coverage(archives)


if __name__ == "__main__":
    main()
