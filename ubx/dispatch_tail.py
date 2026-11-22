"""Read saved Dispatch milestones for the peak and median BS64/128 rounds."""

import json
from pathlib import Path, PurePosixPath
import shlex
import sys

from stage_sources import connect, run_remote


ANALYZE = r'''
from decimal import Decimal
import json
import math
from pathlib import Path
import sys

root = Path(sys.argv[1])
sys.path[:0] = [str(root), str(root / "trees/dispatch/tools/profiling/python")]
from report import OPERATOR, RANKS, capture_mapping, captures, native_rounds, profiler_frequency, rank_dir, stage_role
from tilexr_profiling.pairing import load_profile_run


def timebase(paths, frequency, lanes):
    origins = {rank: min(sample.raw_start for lane in lanes if lane.rank == rank
                        for sample in lane.samples if sample.critical) for rank in RANKS}
    sidecars = [paths[rank].with_suffix("").with_suffix(".clock.json") for rank in RANKS]
    if not all(path.is_file() for path in sidecars):
        return lambda rank, tick: (tick - origins[rank]) / frequency, None, None
    models = [json.loads(path.read_text()) for path in sidecars]
    if (len({model["target"] for model in models}) != 1 or
            any(model["rank"] != rank or model["rank_size"] != len(RANKS) or model["state"] != 2
                or model["cycles_per_second"] != frequency * 1e6
                or not math.isfinite(model["offset_cycles"]) or abs(model["rate_correction"]) >= .001
                or not math.isfinite(model["rate_correction"]) for rank, model in enumerate(models))):
        return lambda rank, tick: (tick - origins[rank]) / frequency, None, None

    def corrected(rank, tick):
        model = models[rank]
        return (Decimal(tick) + Decimal(str(model["offset_cycles"])) +
                Decimal(str(model["rate_correction"])) * (tick - model["anchor_cycle"]))

    starts = [corrected(rank, tick) for rank, tick in origins.items()]
    origin = min(starts)
    scale = Decimal(str(frequency))
    return (lambda rank, tick: float((corrected(rank, tick) - origin) / scale),
            max(model["clock_model_bound_ns"] for model in models) / 1000,
            float((max(starts) - origin) / scale))


def round_metrics(paths, frequency, mapping, native, tag, round_id):
    run = load_profile_run(mapping, list(paths.values()))
    assert run.status == "complete" and len(run.launches) == 1
    lanes = [lane for lane in run.launches[0].lanes if lane.core < 56]
    assert sorted((lane.rank, lane.core) for lane in lanes) == [
        (rank, core) for rank in RANKS for core in range(56)
    ]
    tick, bound, skew = timebase(paths, frequency, lanes)
    rank = max(RANKS, key=lambda rank: native[rank][round_id])
    role_ends = {}
    recv = []
    for lane in lanes:
        stages = {sample.name: sample for sample in lane.samples}
        role = stage_role(("dispatch", "dispatch", 0, "on"), lane.core)
        ready = stages.get("last_record_ready")
        done = stages.get("last_copyout_complete")
        if role == "RECV" and ready and done:
            recv.append((lane, stages, ready, done))
        end = stages.get("final_drain")
        if end and lane.rank == rank:
            role_ends[role] = max(role_ends.get(role, 0), tick(rank, end.raw_start))

    def critical_recv(entries):
        if not entries:
            return None
        lane, stages, ready, done = max(entries, key=lambda row: tick(row[0].rank, row[3].raw_end))
        clock = lambda event: round(tick(lane.rank, event), 3)
        values = {"rank_core": [lane.rank, lane.core], "ready": clock(ready.raw_start),
                  "done": clock(done.raw_end),
                  "after_ready_us": round((done.raw_end - ready.raw_start) / frequency, 3)}
        for key in ("recv_index", "recv_progress", "first_record_ready", "role_drain",
                    "recv_probe_sum", "recv_load_sum", "recv_copyout_sum", "recv_wait_sum"):
            if key in stages:
                sample = stages[key]
                values[key] = (clock(sample.raw_start) if sample.kind == "instant" else
                               round(sample.duration_us, 3))
        if bound is not None:
            same_host = lane.rank // 16
            for key in ("last_forward_complete", "last_packet_detected", "last_batch_forward_submit_end",
                        "intra_send"):
                matching = [
                    (tick(other.rank, sample.raw_end), other.rank, other.core)
                    for other in lanes if other.rank // 16 == same_host for sample in other.samples
                    if sample.name == key
                ]
                if matching:
                    value, peer, core = max(matching)
                    values[key] = [round(value, 3), peer, core]
        return values

    result = {
        "round_type": tag, "round": round_id, "native_slowest": [rank, native[rank][round_id]],
        "clock_bound_us": bound, "start_skew_us": None if skew is None else round(skew, 3),
        "slow_rank_role_done": {role: round(value, 3) for role, value in role_ends.items()},
        "slow_rank_recv": critical_recv([entry for entry in recv if entry[0].rank == rank]),
    }
    if bound is not None:
        global_recv = critical_recv(recv)
        if global_recv and global_recv != result["slow_rank_recv"]:
            result["latest_recv"] = global_recv
    return result


for bs in (64, 128):
    case = ("dispatch", "dispatch", bs, "on")
    folder = root / "runs" / (f"dispatch-dispatch-bs{bs}" + sys.argv[3] + "-on")
    summary = json.loads((folder / "timing.json").read_text())
    assert summary.get("effective_max_tokens", 2) == 2
    frequency = profiler_frequency(folder, case)
    mapping = capture_mapping(case, frequency)
    rounds = captures(folder)
    raw = {rank: native_rounds(rank_dir(folder, rank), OPERATOR["dispatch", "dispatch"]) for rank in RANKS}
    values = {"revision": sys.argv[2], "bs": bs, "rounds": [
        round_metrics(rounds[summary["typical_rounds"][tag]], frequency, mapping, raw, tag,
                      summary["typical_rounds"][tag]) for tag in ("middle_high", "max")
    ]}
    print(json.dumps(values, separators=(",", ":")), flush=True)
'''


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with connect(args["host"]) as client, client.open_sftp() as transfer:
        parent = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx"
        candidates = (("ep32-fa0eaf8a-377fbe6d", "fa0eaf8a", "-max2"),
                      ("ep32-377fbe6d", "e4a9e4c2", ""))
        for namespace, revision, suffix in candidates:
            command = "/usr/bin/python3 -B - " + shlex.join([str(parent / namespace), revision, suffix])
            code, output, _ = run_remote(client, command, timeout=85, input_text=ANALYZE)
            if code:
                raise RuntimeError(f"invalid or incomplete persisted profiling evidence for {revision}")
            for line in output.splitlines():
                print(json.dumps(json.loads(line), separators=(",", ":")))


if __name__ == "__main__":
    main()
