"""Measure TopK overlap and token fanout in the selected completed Dispatch rounds."""

from collections import Counter, defaultdict
from math import ceil
import os
from pathlib import Path
from statistics import median
from zipfile import ZipFile

from assist_b2_relay import packet_edges, streams
from assist_offline import emit, load


def observations(archives, case, step):
    result = defaultdict(list)
    for rank in range(32):
        stem = f"{case}/rank-{rank:02d}/rank-{rank}-round-{step}"
        archive = next(z for z in archives if stem + ".records.bin" in z.namelist())
        capture = streams(archive.read(stem + ".records.bin"))
        freq = load(archive, stem + ".clock.json")["cycles_per_second"] / 1e6
        kinds = defaultdict(list)
        for core in range(56):
            for tick, event in capture[core]:
                kinds[core, event].append(tick)
        origin = min(kinds[core, 1][0] for core in range(56))

        def marks(cores, event):
            return [tick for core in cores for tick in kinds[core, event]]

        def packets(cores, event):
            return [tick for core in cores for tick, kind, _ in packet_edges(capture[112 + core]) if kind == event]

        send = range(16)
        recv = range(16, 32)
        relay = range(32, 48)
        core_zero = range(1)
        publisher = range(16, 17)
        send_first, send_last = packets(send, 7), packets(send, 8)
        values = {
            "local_topk_published": (marks(publisher, 243), max),
            "topk_write_begin": (marks(core_zero, 246), min),
            "topk_write_submitted": (marks(core_zero, 247), min),
            "send0_payload_first": (packets(core_zero, 7), min),
            "payload_submit_first": (send_first, min),
            "payload_submit_last": (send_last, max),
            "topk_first_fanout_done": (marks(relay, 255), min),
            "topk_last_fanout_done": (marks(relay, 255), max),
            "recv_last_source_ready": (marks(recv, 257), max),
            "recv_first_expert_count": (marks(recv, 270), min),
            "recv_last_prefix_done": (marks(recv, 14), max),
            "relay_first_plan_start": (marks(relay, 33), min),
            "relay_last_plan_done": (marks(relay, 34), max),
            "relay_first_payload_flag": (packets(relay, 12), min),
            "relay_first_mem_submit": (packets(relay, 18), min),
            "relay_last_mem_submit": (packets(relay, 18), max),
            "first_copyout_submit": (packets(range(32), 28), min),
            "last_copyout_drain": (packets(range(32), 31), max),
        }
        for name, (ticks, select) in values.items():
            if ticks:
                result[name].append((select(ticks) - origin) / freq)
        spans = {
            "publish_to_topk_submit": (values["local_topk_published"], values["topk_write_submitted"]),
            "first_payload_before_topk_submit": (values["payload_submit_first"], values["topk_write_submitted"]),
            "last_payload_before_last_plan": (values["payload_submit_last"], values["relay_last_plan_done"]),
            "first_forward_before_last_prefix": (values["relay_first_mem_submit"], values["recv_last_prefix_done"]),
            "forward_and_copyout_overlap": (values["first_copyout_submit"], values["relay_last_mem_submit"]),
        }
        for name, ((left, left_pick), (right, right_pick)) in spans.items():
            if left and right:
                result[name].append((right_pick(right) - left_pick(left)) / freq)
    return result


def inspect_fanout(archive, path):
    trace = load(archive, path)
    stats = defaultdict(lambda: {"tokens": 0, "split": 0, "other_core_counts": Counter(),
                                 "slice_us": [], "total_us": []})
    for row in trace["relay_tokens"]:
        entry = stats[row["routes"]]
        entry["tokens"] += 1
        entry["split"] += len(row["cores"]) > 1
        entry["other_core_counts"][len(row["other_relay_cores_submitting"])] += 1
        times = [item["submit_us"] for item in row["slices"]]
        entry["slice_us"].extend(times)
        entry["total_us"].append(sum(times))
    return stats


def summarize(values):
    ordered = sorted(values)
    return [round(median(ordered), 3), round(ordered[ceil(len(ordered) * .9) - 1], 3)]


def main():
    output = Path(os.environ["USERPROFILE"]) / "Downloads"
    with ZipFile(output / "TileXR-UBX-EP32-Dispatch-assist-b2dea4e8.zip") as report, ZipFile(
        output / "TileXR-b2dea4e8-profiling-on-184-complete-f3f4de52.zip"
    ) as first, ZipFile(output / "TileXR-b2dea4e8-profiling-on-84-complete-f3f4de52.zip") as second:
        archives = first, second
        for bs in (32, 64, 128):
            case = f"runs/tail-reviewed-dispatch-bs{bs}-on"
            timing = load(report, case + "/timing.json")
            metrics, fanout = defaultdict(list), defaultdict(list)
            for label, summary in timing["packet_diagnostics"].items():
                step = summary["round"]
                for name, values in observations(archives, case, step).items():
                    metrics[name].extend(values)
                path = f"{case}/tail-report/{label}-round-{step}.json"
                archive = next((z for z in (report, *archives) if path in z.namelist()), None)
                if archive is None:
                    continue
                for routes, stats in inspect_fanout(archive, path).items():
                    fanout[routes].append(stats)
            emit(f"bs{bs}", {
                "timings_median_p90_us": {key: summarize(values) for key, values in metrics.items() if values},
                "samples": {key: len(values) for key, values in metrics.items()},
                "fanout_by_route_count": {routes: {
                    "tokens": sum(entry["tokens"] for entry in rows),
                    "split": sum(entry["split"] for entry in rows),
                    "other_cores_median_p90": summarize(list(sum(
                        (entry["other_core_counts"] for entry in rows), Counter()).elements())),
                    "slice_us_median_p90": summarize([item for entry in rows for item in entry["slice_us"]]),
                    "token_sum_us_median_p90": summarize([item for entry in rows for item in entry["total_us"]]),
                } for routes, rows in sorted(fanout.items())},
            })


if __name__ == "__main__":
    main()
