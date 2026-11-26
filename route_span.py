import csv
import json
from pathlib import Path
import sys

from controller import OUTPUT
from run_matrix import RESULT, stats


def measure(batch, package):
    directory = RESULT / f"bs{batch}-{package}-on"
    if not (directory / "summary.json").is_file():
        return None

    sys.path.insert(0, str(OUTPUT / "source.git/tools/profiling/python"))
    from tilexr_profiling.records import parse_record_bytes

    samples = []
    for rank in range(32):
        rank_dir = directory / "raw" / f"node{rank // 16}" / f"rank-{rank}"
        for round_id in range(5, 25):
            stem = rank_dir / f"rank-{rank}-round-{round_id}"
            clock = json.loads(stem.with_suffix(".clock.json").read_text(encoding="utf-8"))
            records = parse_record_bytes(stem.with_suffix(".records.bin").read_bytes())
            for lane in records.streams:
                core = lane.id & 0xFFFF
                if 16 <= core < 32:
                    events = {record.event_id: record.timestamp for record in lane.records}
                    start, end = events[240], events[14]
                    assert end >= start
                    samples.append((rank, round_id, core, (end - start) * 1e6 / clock["cycles_per_second"]))
    assert len(samples) == 32 * 20 * 16
    with (directory / "recv-topk-to-prefix-end.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["rank", "round", "core", "duration_us"])
        writer.writerows(samples)
    return {"samples": len(samples), "min_median_max_us": stats([row[-1] for row in samples])}


if __name__ == "__main__":
    assert sys.argv[1] == "--args-file"
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    print(json.dumps({f"bs{batch}-{package}": measure(batch, package)
                      for batch in args["batches"] for package in ("baseline", "candidate")},
                     separators=(",", ":")))
