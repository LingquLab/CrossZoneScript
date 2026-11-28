"""Extract rank-local marker durations from the saved Gather profiling runs."""
from collections import defaultdict
import json
from pathlib import Path
import re
import statistics
import sys

base = Path.home() / "tilexr-validation"
case = base / "ht-0b9c3322-ep8-1795846815"
build = case / "profile-build"
cache = (build / "CMakeCache.txt").read_text()
source = Path(re.search(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$", cache, re.M).group(1))
sys.path.insert(0, str(source / "tools/profiling/python"))
from tilexr_profiling.records import load_record_files

pairs = {"gather_total": (1, 2), "gather_chunk_send": (18, 19), "gather_final_quiet": (7, 8),
         "gateway_total": (1, 2), "gateway_gather_wait": (3, 4), "gateway_reduce_and_publish": (4, 5)}
rows = {}
for reducers in (12, 16, 24, 40):
    paths = sorted((case / f"gather-w8-r{reducers}").glob("gather.*.records"))
    samples = defaultdict(list)
    overflow = 0
    incomplete = 0
    for path in paths:
        if ".round0." in path.name:
            continue
        for capture in load_record_files([path]):
            for stream in capture.streams:
                overflow += stream.overflow or stream.dropped_count != 0
                lane = stream.id & 0xffff
                role = "gather" if lane < 8 else "gateway"
                if (role == "gateway" and lane >= 8 + reducers) or not stream.records:
                    continue
                incomplete += not stream.complete
                for name, (begin, end) in pairs.items():
                    if not name.startswith(role):
                        continue
                    pending = None
                    for record in stream.records:
                        if record.event_id == begin:
                            pending = record.timestamp
                        elif record.event_id == end and pending is not None:
                            samples[name].append(record.timestamp - pending)
                            pending = None
    rows[reducers] = {"files": len(paths), "overflow_streams": overflow, "incomplete_streams": incomplete,
                      "cycles_n_median_max": {k: [len(v), statistics.median(v), max(v)]
                                              for k, v in samples.items()}}
print("TILEXR_RESULT " + json.dumps(rows, separators=(",", ":")))
