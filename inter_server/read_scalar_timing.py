"""Read timing fields and clock settings from completed CPU SST artifacts."""

import json
import subprocess

source = r"""
from collections import Counter
import json
from pathlib import Path
import re
import subprocess

root = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native")
time_marker = re.compile(r"simulated time|simulation is complete|simulation time|run time|runtime|elapsed|wall.?time|total time|execution time|cycle count|finish.*cycle|kernel.*(?:start|end|finish)|(?:frequency|clock).*?(?:MHz|GHz)|(?:start|end|finish).*?(?:time|cycle)", re.I)
clock_marker = re.compile(r"^\s*[\w_]*(?:cycle|clock|freq|timebase)[\w_]*\s*:", re.I)
result = {}
for name in ("t4", "t8"):
    case = root / (name + "-sst-mte-scalar-forward-5a6c7c8c")
    run = json.loads((case / "run.json").read_text())
    assert run["status"] == "sst_and_checker_passed"
    file_times = {}
    for relative in ("sim.log", "native-runner.log", "run.json", "check.log", "task-result.json"):
        path = case / relative
        st = path.stat()
        born = int(subprocess.check_output(["stat", "-c", "%W", str(path)], text=True).strip())
        file_times[relative] = {"created_unix_s": born, "modified_unix_s": round(st.st_mtime, 3)}
    timing_lines = {}
    for relative in ("sim.log", "native-runner.log"):
        all_lines = (case / relative).read_text(errors="replace").splitlines()
        filtered = [line.strip()[:220] for line in all_lines if time_marker.search(line)]
        timing_lines[relative] = {"matches": len(filtered), "first": filtered[:6], "last": filtered[-16:]}
    clocks = {}
    for filename in sorted((case / "v310_config").glob("*.yml")):
        if "workload" in filename.name:
            continue
        rows = sorted({line.strip()[:120] for line in filename.read_text(errors="replace").splitlines()
                       if clock_marker.search(line) and re.search(r"\d", line)})
        if rows:
            clocks[filename.name] = rows[:25]
    trace_fields = Counter()
    for path in sorted((case / "OUTPUT").rglob("*.json")):
        if "Host0_" not in path.name:
            continue
        with path.open("rb") as stream:
            trace = stream.read(512 * 1024).decode("utf-8", "replace")
        trace_fields.update(set(re.findall(r'"([A-Za-z][A-Za-z0-9_]*(?:[Tt]ime|[Cc]ycle|[Kk]ernel|[Dd]uration)[A-Za-z0-9_]*)"\s*:', trace)))
    result[name] = {
        "timing_metadata": {k: v for k, v in run.items() if re.search("elapsed|duration|time|start|end|cycle", k, re.I)},
        "file_times": file_times,
        "timing_lines": timing_lines,
        "clocks": clocks,
        "trace_timing_fields": dict(trace_fields),
    }
print(json.dumps(result, ensure_ascii=True))
"""
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode("ascii"), capture_output=True, timeout=45)
proc.check_returncode()
print(json.dumps(json.loads(proc.stdout), ensure_ascii=True))
