"""Export the eight native traces for each selected logical server and round."""
import csv
import json
import math
from pathlib import Path
import shutil
import sys


def clip_trace(directory, sample, total_rounds):
    events = []
    for path in sorted(directory.rglob("msprof_*.json")):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        candidates = data if isinstance(data, list) else data.get("traceEvents", [])
        events.extend(event for event in candidates if isinstance(event, dict))

    timed = []
    metadata = []
    for event in events:
        if event.get("ph") == "M":
            metadata.append(event)
            continue
        try:
            start = float(event["ts"])
            duration = float(event.get("dur") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(start) and math.isfinite(duration):
            timed.append((start, duration, {**event, "ts": start, "dur": duration}))

    kernels = sorted(
        ((start, duration) for start, duration, event in timed if event.get("ph") == "X" and
         "tilexr_moonep_combine_ht_kernel" in str(event.get("name", ""))),
    )
    if len(kernels) != total_rounds:
        return None
    begin, duration = kernels[sample["iteration"]]
    if abs(duration - sample["kernel_us"]) > max(1, sample["kernel_us"] * .01):
        return None
    end = begin + duration
    window = [event for start, elapsed, event in timed if start <= end and start + elapsed >= begin]
    return {"traceEvents": metadata + window, "displayTimeUnit": "ms"}


def export(directory, output=None):
    report = json.loads((directory / "report.json").read_text())
    with (directory / "rank-times.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    keys = ("chunk", "cores", "server", "round")
    if output is None:
        output = directory / "selected-traces"
    output.mkdir()
    selection = report["selected_rounds"]
    clipped = 0
    for placement in ("min", "max", "near_mean"):
        selected = selection[placement]
        ranks = [row for row in rows if all(row[key] == str(selected[key]) for key in keys)]
        if len(ranks) != 8 or len({row["rank"] for row in ranks}) != 8:
            raise RuntimeError(f"expected eight rank samples for {placement}")
        subset = output / placement
        subset.mkdir()
        for row in ranks:
            rank = row["rank"]
            source = (directory / f"chunk-{row['chunk']}-cores-{row['cores']}" /
                      f"server-{row['server']}" / f"rank-{rank}" / "profile")
            sample = {**row, "iteration": int(row["iteration"]), "kernel_us": float(row["kernel_us"])}
            trace = clip_trace(source, sample, report["warmup"] + report["rounds"])
            if trace is None:
                if not any(source.rglob("*.json")):
                    raise RuntimeError(f"missing msprof trace for rank {rank}")
                shutil.copytree(source, subset / f"rank-{rank}")
                row["scope"] = "full_native_capture_contains_selected_iteration"
            else:
                (subset / f"rank-{rank}.trace.json").write_text(json.dumps(trace))
                row["scope"] = "selected_iteration"
                clipped += 1
        (subset / "selection.json").write_text(json.dumps({"selection": selected, "ranks": ranks}, indent=2) + "\n")
    print(json.dumps({"stage": "trace_selection", "groups": 3, "ranks_per_group": 8,
                      "exact_round_slices": clipped}))


if __name__ == "__main__":
    export(Path(sys.argv[1]))
