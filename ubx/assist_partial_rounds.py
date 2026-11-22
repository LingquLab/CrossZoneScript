"""Analyze only the correctness-verified rounds in the interrupted BS128 attempt."""

from collections import Counter, defaultdict
import os
from pathlib import Path
import re
from statistics import median
from zipfile import ZipFile

from assist_offline import emit, load, raw_metrics, span

ATTEMPT = "attempts/transport-e4f330a6534c4b63a33f387366792b24/tail-reviewed-dispatch-bs128-on"


def collect(archives):
    entries = {}
    family = re.compile(re.escape(ATTEMPT) + r"/rank-(\d+)/rank-(\d+)-round-(\d+)\.summary\.json")
    for archive in archives:
        for path in archive.namelist():
            match = family.fullmatch(path)
            if match is None:
                continue
            parent, rank, step = map(int, match.groups())
            summary = load(archive, path)
            if parent != rank or summary["rank"] != rank or summary["round"] != step:
                raise ValueError("mismatched rank or round identity")
            if summary["correctness"] is True and summary["max_tokens"] == 2:
                entries[step, rank] = (archive, path.removesuffix(".summary.json"), summary)
    return entries


def analyze(archives):
    entries = collect(archives)
    complete = [step for step in range(5, 25) if all((step, rank) in entries for rank in range(32))]
    last_role = Counter()
    measured = defaultdict(list)
    by_round = []
    for step in complete:
        capture_ids, targets = set(), set()
        slow_stream, slow_run, slow_drain = None, None, None
        for rank in range(32):
            archive, path, summary = entries[step, rank]
            calibration = load(archive, path + ".clock.json")
            identity, target, data = raw_metrics(archive.read(path + ".records.bin"), rank, calibration)
            capture_ids.add(identity)
            targets.add(target)
            roles = {role: [row for row in data if row["role"] == role and row["copyouts"]]
                     for role in ("SEND", "RECV", "INTRA")}
            final = {role: max(items, key=lambda row: row["copy_done"]) for role, items in roles.items()}
            winner = max(final.values(), key=lambda row: row["copy_done"])
            last_role[winner["role"]] += 1
            for role, rows in roles.items():
                measured[f"{role}_latest_drain_us"].append(final[role]["copy_done"])
                measured[f"{role}_median_first_probe_us"].append(median(row["first_probe"] for row in rows))
            measured["intra_tail_vs_other_us"].append(final["INTRA"]["copy_done"] - max(
                final["SEND"]["copy_done"], final["RECV"]["copy_done"]))
            measured["probe_batches"].append(sum(row["probes"] for row in data))
            measured["empty_probes"].append(sum(row["empty_probes"] for row in data))
            measured["probed_rows"].append(sum(row["probed_rows"] for row in data))
            measured["rows"].append(sum(row["copyouts"] for row in data))
            run = max(row["run_dur"] for row in data)
            if slow_stream is None or summary["dispatch_event_us"] > slow_stream[0]:
                slow_stream = summary["dispatch_event_us"], rank
            if slow_run is None or run > slow_run[0]:
                slow_run = run, rank
            if slow_drain is None or winner["copy_done"] > slow_drain["copy_done"]:
                fields = ("role", "core", "intra_end", "mem_done", "first_probe", "last_ready",
                          "copy_done", "recv_dur", "forwarded", "copyouts", "probes")
                slow_drain = {"rank": rank, **{key: winner[key] for key in fields if key in winner}}
                slow_drain["same_rank_latest_copyout_us"] = {
                    role: round(row["copy_done"], 3) for role, row in final.items()}
        if len(capture_ids) != 1 or len(targets) != 1:
            raise ValueError("mixed attempts or clock targets within a round")
        by_round.append({"round": step, "stream": slow_stream, "run": slow_run, "last_copy": slow_drain})
    ordered = sorted(by_round, key=lambda row: row["stream"][0])
    emit("bs128_verified_subset", {
        "rounds": complete, "last_copyout_rank_rounds": last_role,
        "stream_event_us": span([row["stream"][0] for row in ordered]),
        "kernel_run_window_us": span([row["run"][0] for row in ordered]),
        "per_rank_min_med_max": {metric: span(values) for metric, values in measured.items()},
        "examples": [ordered[i] for i in sorted({len(ordered) // 2 - 1, len(ordered) // 2, len(ordered) - 1})]
        if ordered else [],
    })


def main():
    folder = Path(os.environ["USERPROFILE"]) / "Downloads"
    with ZipFile(folder / "TileXR-e8ee4fff-profiling-on-184-2f281cf1.zip") as first, ZipFile(
        folder / "TileXR-e8ee4fff-profiling-on-84-2f281cf1.zip"
    ) as second:
        analyze((first, second))


if __name__ == "__main__":
    main()
