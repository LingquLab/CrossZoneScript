# Copyright (c) 2026 TileXR Project.
"""Adapt SST dumps of the existing profiling DeviceView ABI 2 to rank/lane reports."""

import collections
import csv
import struct

LANES = 64
STATE_STRIDE = 16


def capacity(spec):
    """Mirror the maintained HT fixture's per-stream event bound with spare records."""
    bound = 8
    for gateway, stages in enumerate(spec.backward):
        if gateway == spec.rank % 8:
            continue
        batches = sum((len(rows) + 127) // 128 + bool(rows) for rows in stages)
        bound = max(bound, 9 * batches + 4 * spec.chunks + 4)
    for reducer in range(spec.reducers):
        records = 3 * spec.chunks + 3
        first = True
        for index in range(64):
            q, i = divmod(index, 16)
            source = i // 8 * 32 + ((spec.rank % 32 // 8 + q) % 4) * 8 + i % 8
            flat = [row for stage in spec.forward[source] for row in stage]
            begin = 0
            part = (reducer + index) % spec.reducers
            for stage in spec.forward[source]:
                count = len(stage)
                task = begin + count * part // spec.reducers
                end = begin + count * (part + 1) // spec.reducers
                if task != end:
                    if task != begin:
                        task = flat[task - 1][0]
                    while task < end:
                        row = flat[task]
                        records += 2 + first
                        first = False
                        if row[3] >= 0:
                            records += 1 + row[0] - task
                        task = row[0]
                begin += count
        bound = max(bound, records)
    result = (bound + 31) // 16 * 16
    if result > 65536:
        raise ValueError("profiling_record_limit_exceeded: " + str(result))
    return result


def layout(cap):
    result = dict(capacity=cap, stride=cap, state_stride=STATE_STRIDE, timestamps=512)
    result["events"] = result["timestamps"] + LANES * cap * 8
    result["counts"] = result["events"] + LANES * cap * 4
    result["dropped"] = result["counts"] + LANES * STATE_STRIDE * 4
    result["states"] = result["dropped"] + LANES * STATE_STRIDE * 4
    result["bytes"] = result["states"] + LANES * STATE_STRIDE * 4
    return result


def image(address, cap):
    offsets = layout(cap)
    data = bytearray(offsets["bytes"])
    struct.pack_into("<8I7Q", data, 0, 2, 88, 1, LANES, cap, cap, STATE_STRIDE, 0,
                     address + offsets["timestamps"], address + offsets["events"],
                     address + offsets["counts"], address + offsets["dropped"],
                     address + offsets["states"], 0, 1)
    return bytes(data)


PAIRS = {1: (2, "kernel_total"), 3: (5, "stage_work"), 7: (8, "final_quiet"),
         12: (13, "wqe_batch_build"), 14: (15, "single_wqe_build"),
         16: (17, "sq_submit"), 18: (19, "chunk_send"), 22: (23, "token_ready_to_reduced")}


def summarize(raw, cap, rank, folder):
    offsets = layout(cap)
    assert len(raw) == offsets["bytes"], "profiling_dump_size"
    lanes = []
    intervals = collections.defaultdict(list)
    events_path = folder / ("rank" + str(rank) + ".profiling-events.csv")
    with events_path.open("w", newline="") as handle:
        csv_writer = csv.writer(handle)
        csv_writer.writerow(("rank", "lane", "index", "event", "tick"))
        for lane in range(LANES):
            state_offset = lane * STATE_STRIDE * 4
            count = struct.unpack_from("<I", raw, offsets["counts"] + state_offset)[0]
            dropped = struct.unpack_from("<I", raw, offsets["dropped"] + state_offset)[0]
            state = struct.unpack_from("<I", raw, offsets["states"] + state_offset)[0]
            assert state == 2, "profiling_lane_not_committed: rank=%s lane=%s state=%s" % (rank, lane, state)
            assert dropped == 0 and 2 <= count <= cap, "profiling_count_or_drop"
            ticks = struct.unpack_from("<" + str(count) + "Q", raw, offsets["timestamps"] + lane * cap * 8)
            events = struct.unpack_from("<" + str(count) + "I", raw, offsets["events"] + lane * cap * 4)
            assert events[0] == 1 and events[-1] == 2, "profiling_missing_begin_end"
            assert all(a <= b for a, b in zip(ticks, ticks[1:])), "profiling_nonmonotonic_ticks"
            opens = collections.defaultdict(list)
            ends = {end: (begin, name) for begin, (end, name) in PAIRS.items()}
            for index, (event, tick) in enumerate(zip(events, ticks)):
                csv_writer.writerow((rank, lane, index, event, tick))
                if event in PAIRS:
                    opens[event].append(tick)
                elif event in ends:
                    begin, name = ends[event]
                    assert opens[begin], "profiling_unmatched_end"
                    intervals[name].append(tick - opens[begin].pop())
                elif event >= 256:
                    if event % 2 == 0:
                        opens[event].append(tick)
                    else:
                        assert opens[event - 1], "profiling_unmatched_cq"
                        intervals["doorbell_to_cq"].append(tick - opens[event - 1].pop())
            assert not any(opens.values()), "profiling_unclosed_interval"
            lanes.append(dict(lane=lane, records=count, start_tick=ticks[0], end_tick=ticks[-1],
                              elapsed_ticks=ticks[-1] - ticks[0]))
    start, end = min(v["start_tick"] for v in lanes), max(v["end_tick"] for v in lanes)
    stages = {name: dict(count=len(values), min_ticks=min(values), max_ticks=max(values),
                        sum_lane_ticks=sum(values)) for name, values in intervals.items()}
    return dict(rank=rank, committed_lanes=LANES, dropped_records=0, start_tick=start, end_tick=end,
                elapsed_ticks=end - start, lanes=lanes, stages=stages)
