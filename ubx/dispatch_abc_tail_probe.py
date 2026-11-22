"""Attribute the TopK A/B/C timing differences using retained per-rank records."""

import json
from pathlib import Path, PurePosixPath
import shlex
import sys

from dispatch_abc_setup import NAMESPACE
from stage_sources import connect, run_remote

PROBE = r'''
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median
import sys

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from report import MEASURED, native_rounds, rank_dir
sys.path.insert(0, str(root / "trees/tail-a/tools/profiling/python"))
from tilexr_profiling.records import load_record_files

milestones = {
    "run_begin": 1, "run_end": 2, "send_schedule_end": 30, "relay_plan_end": 34,
    "intra_done": 48, "first_intra_payload": 56, "last_copyout": 59,
    "first_relay_payload": 54, "topk_submit_done": 247,
    "fanout_done": 255, "recv_topk_ready": 257, "prefix_done": 14,
}


def native_evidence(directory, timing):
    tasks = [native_rounds(rank_dir(directory, rank), "tilexr_ep_ubx_kernel") for rank in range(32)]
    steps = [int(step) for step in timing["native_task_round_slowest_rank_us"]]
    maxima = {step: max(rank[step] for rank in tasks) for step in steps}
    ordered = sorted(maxima, key=lambda step: (maxima[step], step))
    evidence = []
    for step in (ordered[9], ordered[10], ordered[-1]):
        top = sorted(((tasks[rank][step], rank) for rank in range(32)), reverse=True)[:3]
        evidence.append(dict(round=step, top=top))
    return maxima, evidence, tasks


def on_evidence(directory, timing):
    intervals = defaultdict(list)
    last = []
    fanout_order = Counter()
    for label in ("median_low", "median_high", "max"):
        info = timing["packet_diagnostics"][label]
        step = info["round"]
        worst_rank = info["native_slowest_rank"]
        latest_rank_times = {}
        for rank in range(32):
            stem = rank_dir(directory, rank) / f"rank-{rank}-round-{step}"
            capture, = load_record_files([stem.with_suffix(".records.bin")])
            frequency = json.loads(stem.with_suffix(".clock.json").read_text())["cycles_per_second"] / 1e6
            tracks = [sorted((record.timestamp, record.event_id) for record in stream.records)
                      for stream in sorted(capture.streams, key=lambda stream: stream.id & 0xffff)[:56]]
            marks = [defaultdict(list) for _ in tracks]
            for track, marked in zip(tracks, marks):
                for at, identifier in track:
                    if identifier in milestones.values():
                        marked[identifier].append(at)
            origin = min(tick for marked in marks for tick in marked[milestones["run_begin"]])
            groups = {
                "submit_done": (range(32), "topk_submit_done", max),
                "fanout_last": (range(32, 56), "fanout_done", max),
                "recv_ready": (range(16, 32), "recv_topk_ready", max),
                "prefix_done": (range(16, 32), "prefix_done", max),
                "send_done": (range(16), "send_schedule_end", max),
                "intra_first": (range(48, 56), "first_intra_payload", min),
                "intra_done": (range(48, 56), "intra_done", max),
                "relay_plan_done": (range(32, 48), "relay_plan_end", max),
                "relay_first": (range(32, 48), "first_relay_payload", min),
                "copyout_done": (range(32), "last_copyout", max),
            }
            if rank == worst_rank:
                for output, (cores, event, choose) in groups.items():
                    values = [tick for core in cores for tick in marks[core][milestones[event]]]
                    if values:
                        latest_rank_times[output] = round((choose(values) - origin) / frequency, 3)
                endings = [(tick, core) for core, marked in enumerate(marks)
                           for tick in marked[milestones["run_end"]]]
                tick, core = max(endings)
                latest_rank_times["last_core"] = [core, round((tick - origin) / frequency, 3)]
            if directory.name.startswith("tail-c-"):
                for core in range(48, 56):
                    sent = marks[core][milestones["first_intra_payload"]]
                    fanout = marks[core][milestones["fanout_done"]]
                    if not sent:
                        continue
                    if len(fanout) != 2:
                        fanout_order["incomplete"] += 1
                    else:
                        before = sum(tick < sent[0] for tick in fanout)
                        fanout_order[str(before)] += 1
                        intervals["last_fanout_minus_intra_first"].append((fanout[-1] - sent[0]) / frequency)
        last.append(dict(round=step, rank=worst_rank, us=round(info["native_us"], 3),
                         phases=latest_rank_times, tail_relay_to_submit=info["tail"]["relay_flag_to_mem_submit_us"]))
    median_intervals = {key: round(median(values), 3) for key, values in intervals.items()}
    return dict(rounds=last, fanout_before_first_payload=fanout_order, median_deltas_us=median_intervals)


for bs in (64, 128):
    data = {}
    for variant in ("a", "b"):
        directory = root / "runs" / f"tail-{variant}-dispatch-bs{bs}-max2-off"
        timing = json.loads((directory / "timing.json").read_text())
        per_round, chosen, tasks = native_evidence(directory, timing)
        data[variant] = per_round, chosen, tasks
    a, b = data.values()
    delta = [b[0][step] - a[0][step] for step in MEASURED]
    peak = b[1][-1]
    peak_round = peak["round"]
    peak_rank = peak["top"][0][1]
    print(json.dumps(dict(bs=bs, mode="off", a=a[1], b=b[1],
                          b_faster_same_index_count=sum(val < 0 for val in delta),
                          a_peak_comparator_us=a[2][peak_rank][peak_round],
                          b_same_rank_typical_us=median(b[2][peak_rank][step] for step in MEASURED)),
                     separators=(",", ":")))
    for variant in ("a", "b", "c"):
        directory = root / "runs" / f"tail-{variant}-dispatch-bs{bs}-max2-on"
        timing = json.loads((directory / "timing.json").read_text())
        print(json.dumps(dict(bs=bs, variant=variant, mode="on", **on_evidence(directory, timing)),
                         separators=(",", ":")))
'''

if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with connect(args["host"]) as client, client.open_sftp() as transfer:
        directory = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / NAMESPACE
        status, output, _ = run_remote(client, "/usr/bin/python3 - " + shlex.quote(str(directory)),
                                       timeout=120, input_text=PROBE)
        if status:
            raise SystemExit(status)
        print(output)
