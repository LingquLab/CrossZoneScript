"""Render measured AIV stages in Chrome Trace Event format for Perfetto."""
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

CORE_BYTES = 16384
CORES = 16
CYCLES_PER_US = 1650
STAGES = (
    "kernel", "send_setup", "send_submit", "send_drain", "send_wait_local_ready",
    "send_read_routes", "send_post_batch", "send_ring_doorbell", "send_submit_so", "send_wait_cq",
    "reduce_setup", "reduce_total", "reduce_wait_remote_so_and_local_ready", "reduce_rows",
    "reduce_load_partial", "reduce_add_partial", "reduce_write_output", "write_status",
)


def decode_core(raw):
    words, lost = struct.unpack_from("<QQ", raw)
    if lost or not 6 <= words <= CORE_BYTES // 8 or words % 2:
        raise ValueError(f"Invalid profile header {words=}, {lost=}")
    stack, slices = [], []
    records = list(struct.iter_unpack("<QQ", raw[16:words * 8]))
    if records[0][1] != 1 or records[-1][1] != 2:
        raise ValueError("Missing kernel bounds")
    previous = 0
    for tick, event in records:
        if not 1 <= event <= 2 * len(STAGES) or tick < previous:
            raise ValueError(f"Invalid mark {tick=}, {event=}")
        previous = tick
        stage = STAGES[(event - 1) // 2]
        if event % 2:
            stack.append((stage, tick))
        else:
            if not stack or stack[-1][0] != stage:
                raise ValueError(f"Unpaired {stage} mark")
            _, begin = stack.pop()
            slices.append((stage, begin, tick))
    if stack:
        raise ValueError("Unclosed stage")
    return slices


def render(case, reconstruct=None):
    if reconstruct is None:
        from exports import reconstruct_die_output
        reconstruct = reconstruct_die_output

    workload = json.loads((case / "workload.yml").read_text())["workload_config"]
    env = json.loads((case / "sst-environment.json").read_text())
    tracks, invalid = {}, []
    for rank in range(64):
        chip = workload[f"chip{rank}"]
        region = chip["output_param"]["profile"]
        address = region["addr"] - chip["sys_va_base_addr"]
        paths = [case / "bin" / f"david_{rank}_die{die}_profile.bin" for die in (1, 2)]
        data = reconstruct(paths, address, region["size"], env["decoder_library"])
        for owner in range(CORES):
            offset = owner * CORE_BYTES
            try:
                tracks[rank, owner] = decode_core(data[offset:offset + CORE_BYTES])
            except ValueError as error:
                invalid.append([rank, owner, str(error)])
    if not tracks:
        raise ValueError("No valid AIV timelines")
    origin = min(begin for slices in tracks.values() for _, begin, _ in slices)
    last = max(tracks, key=lambda track: tracks[track][-1][2])
    events, breakdown = [], defaultdict(list)
    last_breakdown = {}
    for (rank, owner), slices in tracks.items():
        if owner == 0:
            events.append(dict(ph="M", name="process_name", pid=rank, tid=0, args={"name": f"Rank {rank}"}))
        events.append(dict(ph="M", name="thread_name", pid=rank, tid=owner, args={"name": f"AIV {owner}"}))
        totals = defaultdict(float)
        for stage, begin, end in sorted(slices, key=lambda row: (row[1], -row[2])):
            duration = (end - begin) / CYCLES_PER_US
            totals[stage] += duration
            events.append(dict(name=stage, ph="X", pid=rank, tid=owner,
                               ts=round((begin - origin) / CYCLES_PER_US, 6), dur=round(duration, 6)))
        for stage, duration in totals.items():
            breakdown[stage].append(duration)
        if (rank, owner) == last:
            last_breakdown = {stage: round(duration, 3) for stage, duration in totals.items()}
    summary = dict(streams=len(tracks), invalid_count=len(invalid), invalid_sample=invalid[:6],
                   slices=sum(map(len, tracks.values())), last_aiv=list(last), last_aiv_us=last_breakdown,
                   clock_cycles_per_us=CYCLES_PER_US,
                   stages={name: {"tracks": len(values), "median_us": round(median(values), 3),
                                  "max_us": round(max(values), 3)} for name, values in breakdown.items()})
    (case / "perfetto_trace.json").write_text(json.dumps({"traceEvents": events}, separators=(",", ":")))
    (case / "profile-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, separators=(",", ":")))


if __name__ == "__main__":
    render(Path(sys.argv[1]))
