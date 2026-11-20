"""Approximate wall time from retained case markers and look for kernel timestamps."""
import json
import subprocess

source = r"""
from collections import Counter
import json
from pathlib import Path
import re
import subprocess

root = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native")
metadata = re.compile(r"kernel.*(?:start|begin|end|finish|duration)|(?:start|begin|end|finish|duration).*kernel", re.I)
result = {}
for name in ("t4", "t8"):
    case = root / (name + "-sst-mte-scalar-forward-5a6c7c8c")
    run = json.loads((case / "run.json").read_text())
    assert run["status"] == "sst_and_checker_passed"
    marker_names = ("case-manifest.json", "blue-build-record.json", "sim.log",
                    "check.log", "run.json", "task-result.json")
    marks = {key: (case / key).stat().st_mtime_ns / 1e9 for key in marker_names}
    artifacts = Counter()
    marker_hits = 0
    trace_units = set()
    for path in sorted((case / "OUTPUT").rglob("*")):
        if not path.is_file():
            continue
        artifacts[(path.suffix or "no extension")] += 1
        if not path.suffix in {".json", ".log", ".txt", ".csv"}:
            continue
        with path.open("rb") as stream:
            content = stream.read(4 * 1024 * 1024).decode("utf-8", "replace")
        marker_hits += len(metadata.findall(content))
        trace_units.update(re.findall(r'"displayTimeUnit"\s*:\s*"([^"]+)"', content))
    done = next(line for line in (case / "sim.log").read_text(errors="replace").splitlines()
                if "Simulation is complete, simulated time:" in line)
    docker_state = json.loads(subprocess.check_output(
        ["docker", "inspect", "--format", "{{json .State}}", "tilexr-cpu-b4f4660a-lr8"], text=True, timeout=6))
    result[name] = {
        "sst_reported_time": done.strip(),
        "mtime_unix_s": {key: round(val, 3) for key, val in marks.items()},
        "wall_seconds_case_ready_to_last_sst_output": round(marks["sim.log"] - marks["case-manifest.json"], 3),
        "wall_seconds_case_ready_to_checker_complete": round(marks["run.json"] - marks["case-manifest.json"], 3),
        "wall_seconds_case_ready_to_final_receipt": round(marks["task-result.json"] - marks["case-manifest.json"], 3),
        "docker_state_times": {key: docker_state.get(key) for key in ("StartedAt", "FinishedAt")},
        "artifact_counts": dict(artifacts),
        "kernel_timing_marker_matches_in_artifact_prefixes": marker_hits,
        "trace_display_units": sorted(trace_units),
    }
print(json.dumps(result, ensure_ascii=True))
"""
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode("ascii"), capture_output=True, timeout=55)
proc.check_returncode()
print(json.dumps(json.loads(proc.stdout), ensure_ascii=True))
