"""Validate original case dumps and include the new events in the pairing checks."""

import contextlib
import importlib
import io
import json
import shutil
import sys
import tempfile
import types
from collections import Counter, defaultdict
from pathlib import Path

root = Path("/run-cases")
failed = False
scratch = Path(tempfile.mkdtemp(prefix="urma64p-report-"))
for schedule in ("syncall", "nosync", "clock"):
    preserved = root / schedule
    original_report = json.loads(sorted(preserved.glob("runs/*/run.json"))[-1].read_text())
    if original_report["returncode"] != 0 or original_report.get("error") != "Unknown event":
        raise ValueError("unexpected original run status")
    case = scratch / schedule
    case.mkdir()
    for entry in preserved.iterdir():
        target = case / entry.name
        if entry.is_dir():
            target.symlink_to(entry, target_is_directory=True)
        else:
            shutil.copyfile(entry, target)
    timing = defaultdict(lambda: defaultdict(list))
    counts = Counter()
    lane_count = [0]

    def collect(events, lane):
        rank, expected_lane = divmod(lane_count[0], 31)
        assert lane == expected_lane, "unexpected profiling lane order"
        lane_count[0] += 1
        times = [cycle for cycle, _ in events]
        assert sorted(times) == times, "nonmonotonic profiling timestamps"
        for cycle, event in events:
            if event in (50, 51, 52, 53):
                counts[event] += 1
                timing[rank][event].append(cycle)

    path = case / "full_validation.py"
    source = path.read_text()
    transforms = {
        "pairs = {2: 1, 11: 10, 13: 12, 21: 20, 31: 30, 41: 40}":
            "pairs = {2: 1, 11: 10, 13: 12, 21: 20, 31: 30, 41: 40, 51: 50, 53: 52}",
        "allowed = {1, 2, 10, 11, 12, 13, 20, 21, 30, 31, 40, 41}":
            "allowed = {1, 2, 10, 11, 12, 13, 20, 21, 30, 31, 40, 41, 50, 51, 52, 53}",
        "{30, 31, 40, 41} for e in ids[2:-4]":
            "{30, 31, 40, 41, 50, 51, 52, 53} for e in ids[2:-4]",
        "            for event in ids:": "            collect(events, lane)\n            for event in ids:",
    }
    for old, new in transforms.items():
        if source.count(old) != 1:
            raise ValueError("unrecognized checker layout")
        source = source.replace(old, new)
    sys.path.insert(0, str(case))
    try:
        module = types.ModuleType("full_validation")
        module.__file__ = str(path)
        module.collect = collect
        sys.modules[module.__name__] = module
        exec(compile(source, str(path), "exec"), module.__dict__)
        with contextlib.redirect_stdout(io.StringIO()):
            case_tools = importlib.import_module("case_tools")
            scenario = case_tools.preflight(case)
            report = case_tools.check_outputs(case, scenario, case / "bin")
        assert report["status"] == "full_outputs_verified"
        assert lane_count[0] == 1984 and len(timing) == 64, (lane_count[0], len(timing))
        assert [counts[n] for n in (50, 51, 52, 53)] == [960, 960, 512, 512], counts
        end0 = max(max(events[51]) for events in timing.values())
        start1 = min(min(events[52]) for events in timing.values())
        end_start1 = max(max(events[52]) for events in timing.values())
        local_gaps = [min(events[52]) - max(events[51]) for events in timing.values()]
        timing_summary = dict(last_same_end=end0, first_cross_start=start1, last_cross_start=end_start1,
                              global_gap_cycles=start1-end0, min_rank_gap_cycles=min(local_gaps),
                              cross_start_spread_cycles=end_start1-start1)
        evidence = json.loads((case / "candidate-result.json").read_text())
        print(json.dumps({"schedule": schedule, "verification": report["status"], "lanes": lane_count[0],
                          "event_counts": counts, "simulated_us": evidence["simulated_us"],
                          "deadline": evidence["deadline"], "timing": timing_summary}), flush=True)
    except Exception as exc:
        print(json.dumps({"schedule": schedule, "verification": "failed",
                          "error": type(exc).__name__, "detail": str(exc)[:220]}), flush=True)
        failed = True
    finally:
        sys.path.pop(0)
        for name in ("case_tools", "full_validation", "profiling_view", "jetty_status"):
            sys.modules.pop(name, None)
raise SystemExit(1 if failed else 0)
