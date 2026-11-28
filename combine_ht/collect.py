"""Export the eight native traces for each selected logical server and round."""
import csv
import json
from pathlib import Path
import shutil
import sys


def clip_trace(directory, sample, total_rounds):
    paths = sorted(directory.rglob("msprof_*.json"))
    events = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        events.extend(data if isinstance(data, list) else data["traceEvents"])
    kernels = sorted(
        (event for event in events if event.get("ph") == "X" and
         "tilexr_moonep_combine_ht_kernel" in event.get("name", "")),
        key=lambda event: event["ts"],
    )
    if len(kernels) != total_rounds:
        return None
    kernel = kernels[sample["iteration"]]
    if abs(kernel["dur"] - sample["kernel_us"]) > max(1, sample["kernel_us"] * .01):
        return None
    begin, end = kernel["ts"], kernel["ts"] + kernel["dur"]
    window = [event for event in events if event.get("ph") == "M" or
              ("ts" in event and event["ts"] <= end and event["ts"] + event.get("dur", 0) >= begin)]
    return {"traceEvents": window, "displayTimeUnit": "ms"}


def export(directory):
    report = json.loads((directory / "report.json").read_text())
    with (directory / "rank-times.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    keys = ("chunk", "cores", "server", "round")
    output = directory / "selected-traces"
    output.mkdir()
    selection = report["selected_rounds"]
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
        (subset / "selection.json").write_text(json.dumps({"selection": selected, "ranks": ranks}, indent=2) + "\n")
    print(json.dumps({"stage": "trace_selection", "groups": 3, "ranks_per_group": 8}))


if __name__ == "__main__":
    export(Path(sys.argv[1]))
