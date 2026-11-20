"""Read retained stage totals and mark coverage without running the simulator."""

import json
import subprocess

source = r"""
from collections import defaultdict
import json
from pathlib import Path
from statistics import median

case = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-profile-94e104f0")
result = {}
for name in ("run.json", "task-result.json"):
    path = case / name
    if path.is_file():
        raw = json.loads(path.read_text())
        result[name] = {k: v for k, v in raw.items() if k in {
            "status", "returncode", "error", "run", "runner_exception_type",
            "kernel_sha256", "profile", "sst_simulated_microseconds"}}
path = case / "profile-report" / "perfetto_trace.json"
if path.is_file():
    trace = json.loads(path.read_text())
    sums = defaultdict(lambda: defaultdict(float))
    counts = defaultdict(int)
    for event in trace["traceEvents"]:
        if event.get("ph") == "X":
            name = event["name"]
            sums[name][event["pid"], event["tid"]] += event["dur"]
            counts[name] += 1
    result["stage_owner_totals_us"] = {
        name: {"minimum": round(min(values.values()), 3),
               "median": round(median(values.values()), 3),
               "maximum": round(max(values.values()), 3),
               "owner_tracks": len(values), "slices": counts[name]}
        for name, values in sums.items()
    }
else:
    result["profile_bin_files"] = len(list((case / "bin").glob("*_profile.bin")))
    for name in ("profile-render.log", "native-runner.log"):
        p = case / name
        if p.is_file():
            lines = p.read_text(errors="replace").splitlines()
            result[name] = [line[:220] for line in lines if
                            any(word in line.lower() for word in ("error", "missing", "dropped", "incomplete"))][-8:]
print(json.dumps(result, ensure_ascii=True))
"""
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode("ascii"), capture_output=True, timeout=45)
proc.check_returncode()
print(json.dumps(json.loads(proc.stdout), ensure_ascii=True))
