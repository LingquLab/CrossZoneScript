"""Render the six passing Clos 4+8 cases as per-core Perfetto traces."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from render_core_traces import CYCLES_PER_US, pair_events, read_streams


REVISION = "5bfc51be985935fab35932778723775e32b2b901"
RANKS = 16
CORES = 27
RESULTS = {
    "512kib-per-rank": {"functional": 46.9801, "performance": 46.722},
    "4mib-per-rank": {"functional": 81.244, "performance": 81.1149},
    "16mib-per-rank": {"functional": 226.403, "performance": 224.951},
}


def role(core):
    if core < 7:
        return "Fullmesh send"
    if core < 11:
        return "Clos die0 send"
    if core < 19:
        return "Clos die1 send"
    return "receive"


def read_case(case, size, mode):
    run = sorted(case.glob("runs/*/run.json"))[-1]
    report = json.loads(run.read_text())
    assert report["status"] == "simulation_outputs_verified" and report["returncode"] == 0
    validation = report["validation"]
    assert validation["rank_count"] == RANKS
    assert validation["status"] == ("full_outputs_verified" if mode == "functional" else "full_completion_verified")
    assert validation["device_copyout_executed"] is (mode == "functional")
    scenario = json.loads((case / "scenario.json").read_text())
    assert scenario["rank_size"] == RANKS and scenario["block_dim"] == CORES
    assert scenario["candidate_revision"] == REVISION and len(scenario["ranks"]) == RANKS
    assert scenario["clos_4plus8"] == {"die0": 4, "die1": 8, "grain": 32, "wqe_bytes": 7168}
    with run.with_name("sim.log").open("rb") as stream:
        stream.seek(max(0, run.with_name("sim.log").stat().st_size - 65536))
        elapsed, unit = re.findall(rb"simulated time:\s*(\d+(?:\.\d+)?)\s*(ps|ns|us|ms|s)\b",
                                   stream.read(), re.I)[-1]
    simulated_us = float(elapsed) * {b"ps": 1e-6, b"ns": 1e-3, b"us": 1, b"ms": 1000, b"s": 1e6}[unit.lower()]
    assert abs(simulated_us - RESULTS[size][mode]) < 1e-6, (size, mode, simulated_us)
    return scenario, read_streams(case, scenario, CORES), simulated_us


def render(case, size, mode, output):
    scenario, streams, simulated_us = read_case(case, size, mode)
    origin = min(events[0][0] for events in streams.values())
    events = []
    counts = Counter()
    for (rank, core), markers in streams.items():
        group = rank // 8
        if core == 0:
            events.extend((
                dict(name="process_name", ph="M", pid=rank, tid=0,
                     args={"name": f"Rank {rank:02d} | CPU {group + 1}"}),
                dict(name="process_sort_index", ph="M", pid=rank, tid=0, args={"sort_index": rank}),
            ))
        lane_role = role(core)
        events.append(dict(name="thread_name", ph="M", pid=rank, tid=core,
                           args={"name": f"AIV {core:02d} | {lane_role}"}))
        for stage, start, end in pair_events(markers):
            counts[stage] += 1
            events.append(dict(name=stage, cat=lane_role, ph="X", pid=rank, tid=core,
                               ts=(start - origin) / CYCLES_PER_US, dur=(end - start) / CYCLES_PER_US,
                               args=dict(rank=rank, aiv_core=core, cpu_group=group,
                                         start_cycle=start, end_cycle=end)))
    assert counts["kernel_total"] == RANKS * CORES
    assert counts["input_stage_send_and_drain"] == RANKS * 19
    assert counts["progress_receive_and_copy"] == RANKS * 8
    metadata = dict(rank_count=RANKS, cores_per_rank=CORES, payload=size, mode=mode,
                    copyout_enabled=scenario["copyout_enabled"], simulated_us=simulated_us,
                    cycles_per_us=CYCLES_PER_US, raw_origin_cycle=origin,
                    timestamp_basis="GetSystemCycle; trace zero is the first AIV entry",
                    candidate_revision=REVISION, kernel_sha256=scenario["kernel"]["sha256"])
    path = output / f"16p-clos-4plus8-{size}-{mode}.trace.json"
    with path.open("w", encoding="utf-8") as stream:
        json.dump(dict(traceEvents=events, displayTimeUnit="ns", metadata=metadata),
                  stream, separators=(",", ":"), allow_nan=False)
    trace = json.loads(path.read_text())["traceEvents"]
    tracks = {(event["pid"], event["tid"]) for event in trace if event["name"] == "thread_name"}
    assert tracks == {(rank, core) for rank in range(RANKS) for core in range(CORES)}
    print(json.dumps(dict(file=path.name, tracks=len(tracks), stage_events=sum(counts.values()),
                          simulated_us=simulated_us)), flush=True)
    return path


def main():
    root = Path("/output")
    files = []
    for size, modes in RESULTS.items():
        for mode in modes:
            if size == "16mib-per-rank" and mode == "functional":
                case = Path("/functional16/functional/case")
            else:
                case = Path("/regression") / size / mode / "case"
            files.append(render(case, size, mode, root))
    archive = root / "16p-clos-4plus8-core-traces.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in files:
            bundle.write(path, path.name)
    with ZipFile(archive) as bundle:
        assert bundle.testzip() is None and bundle.namelist() == [path.name for path in files]
    print(json.dumps(dict(archive=archive.name, files=len(files), bytes=archive.stat().st_size,
                          sha256=hashlib.sha256(archive.read_bytes()).hexdigest())), flush=True)


if __name__ == "__main__":
    main()
