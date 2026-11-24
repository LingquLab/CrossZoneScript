"""Render Perfetto traces from the 12 verified Full 64P performance dumps."""

import hashlib
import importlib
import json
import sys
from collections import Counter
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


CASES = {
    "512b-per-peer": {"syncall": "9_hqbv7n", "clock": "j17fb797", "nosync": "od5943r_"},
    "512kib-per-rank": {"syncall": "2z0kxquo", "clock": "4uyzrkrb", "nosync": "ihkrz7ny"},
    "4mib-per-rank": {"syncall": "re91rqaa", "clock": "_ylyhhfh", "nosync": "4ed6a7an"},
    "16mib-per-rank": {"syncall": "ji4ap70o", "clock": "kbjar3yp", "nosync": "qdblagat"},
}
MODE_NAMES = {"syncall": "SyncAll", "clock": "Clock", "nosync": "NoSync"}
STAGES = {
    1: ("kernel_total", 2),
    10: ("input_stage_send_and_drain", 11),
    12: ("local_completion", 13),
    20: ("progress_receive_and_copy", 21),
    30: ("input_stage", 31),
    40: ("data_send_and_drain", 41),
    42: ("diagnostic_wqe_patch", 43),
    44: ("diagnostic_publish_and_stripe_wait", 45),
    46: ("diagnostic_stripe_completion_wait", 47),
    50: ("same_group_send", 51),
    52: ("cross_group_send", 53),
}
CYCLES_PER_US = 1000.0
RANKS = 64
CORES = 31


def read_capture(case):
    result = json.loads((case / "candidate-result.json").read_text())
    sim_report = json.loads(sorted(case.glob("runs/*/run.json"))[-1].read_text())
    assert result["exit"] == 0 and sim_report["status"] == "simulation_outputs_verified", case
    scenario = json.loads((case / "scenario.json").read_text())
    assert scenario["rank_size"] == RANKS and scenario["block_dim"] == CORES
    assert scenario["copyout_enabled"] is False
    assert len(scenario["ranks"]) == RANKS
    streams = read_streams(case, scenario, CORES)

    markers = case / "two_group_events.jsonl"
    if markers.is_file():
        recorded = [json.loads(line) for line in markers.read_text().splitlines()]
        decoded = [[core, [[cycle, event] for cycle, event in events if 50 <= event <= 53]]
                   for (_, core), events in streams.items()]
        assert decoded == recorded, f"profile event mismatch in {case}"
    return result, scenario, streams


def read_streams(case, scenario, cores):
    sys.path.insert(0, str(case))
    try:
        case_tools = importlib.import_module("case_tools")
        profiling = importlib.import_module("profiling_view")
        layout = profiling.compute_layout(cores, 256)
        streams = {}
        for rank, record in enumerate(scenario["ranks"]):
            assert rank == record["rank"]
            paths = [case / "bin" / f"david_{rank}_die{die}_profiling.bin" for die in (1, 2)]
            assert all(path.is_file() for path in paths), (case, rank)
            profile = case_tools.extract_die_output(
                paths, f"rank{rank}/profiling", record["profiling_addr"] - record["sys_va_base_addr"],
                layout["bytes"],
            )
            assert len(profile) == layout["bytes"], (case, rank)
            for core in range(cores):
                streams[rank, core] = list(profiling.stream_events(profile, layout, core))
    finally:
        sys.path.pop(0)
        for module in ("case_tools", "full_validation", "profiling_view", "sim_environment", "jetty_status"):
            sys.modules.pop(module, None)

    return streams


def pair_events(events):
    assert events and events[0][1] == 1 and events[-1][1] == 2
    assert all(events[i][0] <= events[i + 1][0] for i in range(len(events) - 1))
    stack = []
    for cycle, event in events:
        if event in STAGES:
            stack.append((event, cycle))
        else:
            begin, start = stack.pop()
            name, expected_end = STAGES[begin]
            assert expected_end == event, (begin, event)
            yield name, start, cycle
    assert not stack


def render_case(size, mode, case, output):
    result, scenario, streams = read_capture(case)
    assert result["schedule"] == mode and result["size"] == size and result["kind"] == "performance"
    origin = min(events[0][0] for events in streams.values())
    counts = Counter()
    timing = {"same_end": [], "cross_start": []}
    case_meta = dict(mode=mode, rank_count=RANKS, cores_per_rank=CORES,
                     simulation_us=result["simulated_us"], deadline_cycles=result["deadline"],
                     candidate_revision=scenario["candidate_revision"], raw_origin_cycle=origin)
    for (rank, core), events in streams.items():
        pid = rank + 1 + list(MODE_NAMES).index(mode) * RANKS
        if core == 0:
            output.append(dict(name="process_name", ph="M", pid=pid, tid=0,
                               args={"name": f"{MODE_NAMES[mode]} | Rank {rank:02d}"}))
            output.append(dict(name="process_sort_index", ph="M", pid=pid, tid=0, args={"sort_index": pid}))
        role = "send" if core < 23 else "receive"
        output.append(dict(name="thread_name", ph="M", pid=pid, tid=core,
                           args={"name": f"AIV {core:02d} | {role}"}))
        for name, start, end in pair_events(events):
            counts[name] += 1
            output.append(dict(name=name, ph="X", cat=role, pid=pid, tid=core,
                               ts=(start - origin) / CYCLES_PER_US, dur=(end - start) / CYCLES_PER_US,
                               args=dict(rank=rank, group=rank // 32, core=core, mode=mode,
                                         start_cycle=start, end_cycle=end)))
            if name == "same_group_send":
                timing["same_end"].append(end)
            elif name == "cross_group_send":
                timing["cross_start"].append(start)

    expected_same, expected_cross = ((960, 512) if scenario["elements_per_peer"] <= 2048 else (1472, 1024))
    assert counts["kernel_total"] == RANKS * CORES
    assert counts["progress_receive_and_copy"] == 64 * 8
    assert (counts["same_group_send"], counts["cross_group_send"]) == (expected_same, expected_cross)
    if result.get("timing"):
        assert max(timing["same_end"]) == result["timing"]["last_same_end"]
        assert min(timing["cross_start"]) == result["timing"]["first_cross_start"]
        assert max(timing["cross_start"]) == result["timing"]["last_cross_start"]
    if mode == "clock":
        assert min(timing["cross_start"]) >= max(max(timing["same_end"]), result["deadline"])
    case_meta["stage_counts"] = dict(counts)
    return case_meta


def main():
    output_dir = Path("/output")
    files = []
    for size, modes in CASES.items():
        events = []
        captures = [render_case(size, mode, Path("/profiles") / suffix / mode, events)
                    for mode, suffix in modes.items()]
        name = f"64p-urma-{size}.trace.json"
        path = output_dir / name
        metadata = dict(payload=size, cycles_per_us=CYCLES_PER_US,
                        time_basis="Full profiling mapping, per-run first core entry as zero",
                        captures=captures)
        with path.open("w", encoding="utf-8") as out:
            json.dump(dict(traceEvents=events, displayTimeUnit="ns", metadata=metadata),
                      out, separators=(",", ":"), allow_nan=False)
        reread = json.loads(path.read_text())
        assert len(reread["traceEvents"]) == len(events)
        assert sum(event["name"] == "thread_name" for event in events) == len(modes) * RANKS * CORES
        files.append(path)
        print(json.dumps(dict(file=name, tracks=len(modes)*RANKS*CORES,
                              stage_events=sum(sum(c["stage_counts"].values()) for c in captures),
                              kernel_ends=[c["stage_counts"]["kernel_total"] for c in captures])), flush=True)
    archive = output_dir / "64p-urma-core-traces.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in files:
            bundle.write(path, path.name)
    with ZipFile(archive) as bundle:
        assert bundle.testzip() is None and bundle.namelist() == [path.name for path in files]
    print(json.dumps(dict(archive=archive.name, bytes=archive.stat().st_size,
                          sha256=hashlib.sha256(archive.read_bytes()).hexdigest())), flush=True)


if __name__ == "__main__":
    main()
