"""Summarize the saved BS64 maxTokens=2 timing evidence without rerunning it."""

import json
from pathlib import Path, PurePosixPath
import shlex
import sys

from stage_sources import connect, run_remote


ANALYZE = r'''
from collections import Counter
import json
from pathlib import Path
from statistics import median
import sys

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from report import MEASURED, OPERATOR, RANKS, native_rounds, rank_dir


def stats(values):
    return [round(value, 3) for value in (min(values), median(values), max(values))]


def analyze(mode):
    case = root / "runs" / f"dispatch-dispatch-bs64-max2-{mode}"
    timing = json.loads((case / "timing.json").read_text())
    assert timing["effective_max_tokens"] == 2
    assert timing["capture_mode"] == {
        "kernel_profiling": mode, "native_profiler": True, "stage_barrier": True,
    }
    native = [native_rounds(rank_dir(case, rank), OPERATOR["dispatch", "dispatch"]) for rank in RANKS]
    rows = [[rank[step] for rank in native] for step in MEASURED]
    tails = [max(row) for row in rows]
    assert dict(zip(map(str, MEASURED), tails)) == timing["native_task_round_slowest_rank_us"]
    winners = Counter(row.index(max(row)) for row in rows)
    return timing, rows, tails, winners


off = analyze("off")
on = analyze("on")
on_max = max(on[2])
for mode, (timing, rows, tails, winners) in zip(("off", "on"), (off, on)):
    above = [sum(value > on_max for value in row) for row in rows]
    slow = Counter(rank for row in rows for rank, value in enumerate(row) if value > on_max)
    print(json.dumps({
        "mode": mode,
        "native_per_rank_us": stats([value for row in rows for value in row]),
        "round_rank_median_us": stats([median(row) for row in rows]),
        "round_max_us": stats(tails),
        "round_max_us_by_round": tails,
        "round_max_by_node_us": [stats([max(row[start:start + 16]) for row in rows]) for start in (0, 16)],
        "slowest_ranks": winners.most_common(),
        "ranks_over_on_max_per_round": above,
        "ranks_over_on_max_count": len(slow),
        "rounds_over_on_max": sum(count > 0 for count in above),
        "stream_event_round_max_us": timing["round_slowest_rank"],
        "rdma_bytes": timing["rdma_bytes"],
    }, separators=(",", ":")))
'''


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with connect(args["host"]) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx/ep32-fa0eaf8a-377fbe6d"
        command = "/usr/bin/python3 -B - " + shlex.quote(str(root))
        code, output, _ = run_remote(client, command, timeout=40, input_text=ANALYZE)
        if code:
            raise RuntimeError("could not verify saved native profiler timings")
        print(output)


if __name__ == "__main__":
    main()
