"""Separate own-core TopK fanout, payload planning, and first observed READY."""

from collections import Counter, defaultdict
from math import ceil
import os
from pathlib import Path
from statistics import median
from zipfile import ZipFile

from assist_b2_relay import packet_edges, streams
from assist_offline import emit, load


def inspect(archives, folder, round_id, metrics, owners, first_probe):
    for rank in range(32):
        stem = f"{folder}/rank-{rank:02d}/rank-{rank}-round-{round_id}"
        archive = next(z for z in archives if stem + ".records.bin" in z.namelist())
        lanes = streams(archive.read(stem + ".records.bin"))
        frequency = load(archive, stem + ".clock.json")["cycles_per_second"] / 1e6
        marks = defaultdict(list)
        first_submits, last_fanouts = [], []
        for core in range(56):
            for tick, event in lanes[core]:
                marks[core, event].append(tick)

        def delta(name, start, end):
            metrics[name].append((end - start) / frequency)

        delta("topk_control_submit", marks[0, 246][0], marks[0, 247][0])
        delta("local_publish_to_control_begin", marks[16, 243][0], marks[0, 246][0])
        for core in range(32, 48):
            edges = packet_edges(lanes[112 + core])
            events = defaultdict(list)
            for tick, event, _ in edges:
                events[event].append(tick)
            append_begin, append_end = events[44][0], events[45][0]
            role = "fanout" if marks[core, 255] else "no_fanout"
            if marks[core, 255]:
                last_fanouts.append(marks[core, 255][-1])
                delta("fanout_done_to_plan", marks[core, 255][-1], append_begin)
                if events[18]:
                    delta("fanout_done_to_first_mem", marks[core, 255][-1], events[18][0])
            delta("plan", append_begin, append_end)
            delta(role + "_plan", append_begin, append_end)
            if not events[18]:
                continue
            first_submits.append((events[18][0], role))
            if not events[12]:
                raise ValueError("forwarding core missing first positive payload FLAG")
            observed = events[12][0]
            delta("plan_end_to_first_observed_flag", append_end, observed)
            delta("first_observed_flag_to_first_mem", observed, events[18][0])
            if marks[core, 130] and marks[core, 131]:
                begin, end = marks[core, 130][0], marks[core, 131][0]
                delta("plan_end_to_first_probe", append_end, begin)
                delta("first_probe_duration", begin, end)
                first_probe[role] += 1
                if begin <= observed <= end:
                    first_probe[role + "_positive"] += 1
                else:
                    delta("first_probe_end_to_first_observed_flag", end, observed)
        if first_submits:
            first_tick, role = min(first_submits)
            owners[role] += 1
            if last_fanouts:
                delta("first_mem_relative_to_rank_last_fanout", max(last_fanouts), first_tick)


def summarize(values):
    ordered = sorted(values)
    return {"n": len(ordered), "median": round(median(ordered), 3),
            "p90": round(ordered[ceil(len(ordered) * .9) - 1], 3)}


def main():
    output = Path(os.environ["USERPROFILE"]) / "Downloads"
    with ZipFile(output / "TileXR-UBX-EP32-Dispatch-assist-b2dea4e8.zip") as report, ZipFile(
        output / "TileXR-b2dea4e8-profiling-on-184-complete-f3f4de52.zip"
    ) as first, ZipFile(output / "TileXR-b2dea4e8-profiling-on-84-complete-f3f4de52.zip") as second:
        for bs in (32, 64, 128):
            folder = f"runs/tail-reviewed-dispatch-bs{bs}-on"
            timing = load(report, folder + "/timing.json")
            metrics, owners, first_probe = defaultdict(list), Counter(), Counter()
            for row in timing["packet_diagnostics"].values():
                inspect((first, second), folder, row["round"], metrics, owners, first_probe)
            emit(f"bs{bs}", {"us": {name: summarize(values) for name, values in metrics.items() if values},
                             "rank_first_forward_core": owners, "first_probe": first_probe})


if __name__ == "__main__":
    main()
