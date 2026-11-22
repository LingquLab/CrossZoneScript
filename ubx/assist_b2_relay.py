"""Resolve late Dispatch route delays on the producing RELAY core."""

from collections import Counter
import os
from pathlib import Path
import re
from zipfile import ZipFile

from assist_offline import FILE, RECORD, STREAM, emit, load


def streams(data):
    at = FILE.size
    result = {}
    while at < len(data):
        identity, count, dropped, flags, block = STREAM.unpack_from(data, at)
        if dropped or flags != 1 or block != STREAM.size + count * RECORD.size:
            raise ValueError("invalid records lane")
        result[identity & 0xffff] = [(tick, event)
                                     for tick, event, _ in RECORD.iter_unpack(data[at + STREAM.size:at + block])]
        at += block
    return result


def packet_key(event):
    return (event >> 19) & 63, (event >> 13) & 63, (event >> 6) & 127, event & 63


def packet_edges(items):
    return [(tick, (event >> 25) & 63, packet_key(event)) for tick, event in items]


def one_tick(edges, kind, key):
    matches = [tick for tick, actual, identity in edges if actual == kind and identity == tuple(key)]
    if len(matches) != 1:
        raise ValueError("selected route or relay packet does not have a unique event")
    return matches[0]


def core_events(archives, case, rank, step, core):
    stem = f"{case}/rank-{rank:02d}/rank-{rank}-round-{step}"
    archive = next(z for z in archives if stem + ".records.bin" in z.namelist())
    trace = streams(archive.read(stem + ".records.bin"))
    frequency = load(archive, stem + ".clock.json")["cycles_per_second"] / 1e6
    return packet_edges(trace[112 + core]), dict((event, tick) for tick, event in trace[56 + core]), frequency


def durations(counters, frequency, indexes):
    values = {}
    for label, index in indexes.items():
        event = 66 + 2 * index if index < 32 else 133 + 2 * (index - 32)
        if event in counters and event + 1 in counters:
            values[label] = round((counters[event + 1] - counters[event]) / frequency, 3)
    return values


def inspect_route(archives, case, trace, tail):
    step = trace["round"]
    producer, core = tail["producer"]
    route = tail["route"]
    if not tail.get("relay_first_hop_packet"):
        return {"route": route, "producer": tail["producer"]}
    start, tokens = tail["relay_first_hop_packet"]
    edges, counts, rate = core_events(archives, case, producer, step, core)
    key = route[0], producer, start, tokens
    detected = one_tick(edges, 12, key)
    forward = one_tick(edges, 13, key)
    submitted = one_tick(edges, 18, route)
    finished = one_tick(edges, 14, key)
    earlier = [(tick, item) for tick, kind, item in edges if kind == 18 and detected < tick < submitted]
    first = min(tick for tick, kind, _ in edges if kind == 18 and forward <= tick <= finished)
    return {
        "route": route, "producer": tail["producer"], "consumer": tail["consumer"], "packet": [start, tokens],
        "flag_to_forward_begin_us": round((forward - detected) / rate, 3),
        "forward_begin_to_first_submit_us": round((first - forward) / rate, 3),
        "first_submit_to_tail_submit_us": round((submitted - first) / rate, 3),
        "completed_other_packets_before_forward": sum(kind == 14 and detected < tick < forward for tick, kind, _ in edges),
        "other_route_submits_before_forward": sum(tick < forward for tick, _ in earlier),
        "preceding_same_packet_routes": sum(tick >= forward for tick, _ in earlier),
        "producer_core_total_us": durations(counts, rate, {"relay_tasks": 11, "relay_positions": 12,
            "relay_export": 13, "listen_load": 14, "listen_probe": 15, "mem_load": 17, "mem_metadata": 18,
            "mem_pack": 19, "mem_publish": 20, "mem_wait": 21, "pipeline_wait": 31,
            "listen_descriptors": 32, "listen_positions": 33, "relay_forward": 34, "mem_publish_wait": 37,
            "mem_dma_issue": 38, "mem_token_setup": 39, "mem_forward_retire": 41}),
    }


def inspect_off(package, raw_zips):
    case = "runs/tail-reviewed-dispatch-bs64-off"
    timing = load(package, case + "/timing.json")
    children = [name for archive in (package, *raw_zips)
                for name in archive.namelist() if name.startswith(case + "/")]
    outlier = max(timing["native_task_round_slowest_rank_us"],
                  key=timing["native_task_round_slowest_rank_us"].get)
    emit("bs64_off", {
        "timing_keys": sorted(timing), "outlier_round": int(outlier),
        "off_case_file_types": dict(Counter(Path(name).suffix for name in children)),
        "retained_rank_files": sum(bool(re.search(r"/rank-\d+/", name)) for name in children),
        "stage_barrier_provenance": timing.get("capture_mode"),
    })


def main():
    folder = Path(os.environ["USERPROFILE"]) / "Downloads"
    with ZipFile(folder / "TileXR-UBX-EP32-Dispatch-assist-b2dea4e8.zip") as report, ZipFile(
        folder / "TileXR-b2dea4e8-profiling-on-184-complete-f3f4de52.zip"
    ) as first, ZipFile(folder / "TileXR-b2dea4e8-profiling-on-84-complete-f3f4de52.zip") as second:
        archives = first, second
        inspect_off(report, archives)
        for bs in (32, 64, 128):
            case = f"runs/tail-reviewed-dispatch-bs{bs}-on"
            timing = load(report, case + "/timing.json")
            for label, summary in timing["packet_diagnostics"].items():
                step = summary["round"]
                name = f"{case}/tail-report/{label}-round-{step}.json"
                archive = next((z for z in (report, *archives) if name in z.namelist()), None)
                if archive is None:
                    continue
                trace = load(archive, name)
                emit(f"bs{bs}_{label}", {
                    "round": step, "slow_rank": trace["native_slowest_rank"],
                    "tail": inspect_route(archives, case, trace, trace["last_copyout_on_slowest_rank"]),
                })


if __name__ == "__main__":
    main()
