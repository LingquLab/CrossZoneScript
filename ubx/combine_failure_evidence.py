"""Read the archived Combine failures and the matching successful Combine capture."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import sys

from stage_sources import connect, run_remote


REMOTE = r'''
from collections import Counter, defaultdict
import csv
from statistics import median
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

base = Path(sys.argv[1])
first = base / "attempts/initial-ubx/stages-write-bs32-off"
rerun = base / "runs/stages-write-bs32-off"
on = base / "runs/stages-write-bs32-on"
paths = {"first_off": first, "repeat_off": rerun, "on": on}
result = {}
for key, path in paths.items():
    if not path.is_dir():
        continue
    ranks = sorted(path.glob("rank-*"))
    error_classes = Counter()
    windows = []
    for rank in ranks:
        logs = [rank / name for name in ("stderr.log", "stdout.log") if (rank / name).is_file()]
        if not logs:
            continue
        windows.extend(log.stat().st_mtime for log in logs)
        errors = set()
        for log in logs:
            sample = log.read_bytes()[-512000:].decode(errors="replace")
            for match in re.findall(r"UBX_FAILURE.*?error=([^;\n]+)", sample):
                errors.add(match[:110])
        error_classes.update(errors)
    totals = defaultdict(list)
    per_rank = defaultdict(dict)
    for rank in ranks:
        for file in rank.rglob("op_summary*.csv"):
            seen = set()
            with file.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    values = {re.sub(r"[^a-z0-9]", "", (k or "").lower()): v for k, v in row.items()}
                    names = [values.get("optype", ""), values.get("opname", "")]
                    for name in names:
                        if name and ("ubx_combine" in name or "ubx_barrier" in name):
                            task = values.get("taskstarttimeus"), values.get("taskdurationus")
                            if task not in seen and task[1]:
                                seen.add(task)
                                per_rank[rank.name].setdefault(name, []).append(float(task[1]))
                            break
        for name, times in per_rank[rank.name].items():
            totals[name].extend(times)
    durations = {
        name: {"count": len(times), "min_us": min(times), "median_us": median(times), "max_us": max(times)}
        for name, times in totals.items()
    }
    rank_extremes = {rank: {name: max(times) for name, times in values.items()}
                     for rank, values in per_rank.items() if values}
    route = path / "routes.bin"
    result[key] = {
        "ranks": len(ranks), "error_classes": dict(error_classes), "native_tasks": durations,
        "sample_rank_tasks": {rank: rank_extremes[rank] for rank in sorted(rank_extremes)[:3]},
        "route_sha": hashlib.sha256(route.read_bytes()).hexdigest() if route.is_file() else None,
        "correctness": len([p for p in path.glob("rank-*/combine-write-rank-*-round-*.json") if ".clock." not in p.name]),
        "modified_utc": [datetime.fromtimestamp(x, timezone.utc).isoformat() for x in sorted(set(windows))[:1] + sorted(set(windows))[-1:]],
    }
timing = on / "timing.json"
if timing.is_file():
    samples = json.loads(timing.read_text())
    result["passing_on_profile"] = [
        {"role": row["role"], "stage": row["stage"], "max_us": row["round_slowest_core_us"]["max"]}
        for row in samples.get("stages", []) if row["stage"] in
        ("progress", "readiness_tail", "final_reduce", "drain", "contributions")
    ]
for variant in ("off", "on"):
    provenance = base / "installs" / ("stages-" + variant) / "provenance.txt"
    if provenance.is_file():
        lines = provenance.read_text().splitlines()
        result[variant + "_provenance"] = [line for line in lines if line.startswith(("revision=", "cann=")) or "tilexr_ep_ubx_combine_kernel.o" in line]
result["runtime_logs"] = []
if first.is_dir():
    log_times = [p.stat().st_mtime for p in first.glob("rank-*/stdout.log")]
    repeat_times = [p.stat().st_mtime for p in rerun.glob("rank-*/stdout.log")]
    periods = [(min(group) - 20, max(group) + 20) for group in (log_times, repeat_times) if group]
    sources = [Path.home() / "ascend/log/run", Path.home() / "ascend/log/plog"]
    for root in sources:
        if not root.is_dir():
            continue
        for log in sorted(root.glob("**/plog*.log")):
            if len(result["runtime_logs"]) >= 10 or not log.is_file() or log.stat().st_size > 100 * 1024 * 1024:
                continue
            # Restrict exported fields to the fault class, core and bounded error text.
            if not any(low - 180 <= log.stat().st_mtime <= high + 180 for low, high in periods):
                continue
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 500000))
                lines = stream.read().decode(errors="replace").splitlines()
            for line in lines:
                if not re.search(r"507035|aiv.*exception|MTE.*error|vector core|ub address|kernel.*exception", line, re.I):
                    continue
                timestamp = re.search(r"\d{4}-\d\d-\d\d[- T]\d\d:\d\d:\d\d", line)
                if timestamp is None:
                    continue
                try:
                    stamp = timestamp[0]
                    when = datetime.strptime(stamp[:10] + " " + stamp[11:],
                                             "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                except ValueError:
                    continue
                if any(low <= when <= high for low, high in periods):
                    diagnostic = {"file": log.name, "time": timestamp[0], "error": line[-225:]}
                    if diagnostic not in result["runtime_logs"]:
                        result["runtime_logs"].append(diagnostic)
                if len(result["runtime_logs"]) == 10:
                    break
print(json.dumps(result, separators=(",", ":")))
'''


def inspect(host, namespace):
    with connect(host) as client, client.open_sftp() as transfer:
        root = transfer.normalize(".") + "/.local/state/tilexr-ubx/" + namespace
        command = "/usr/bin/python3 - " + shlex.quote(root)
        code, output, _ = run_remote(client, command, timeout=45, input_text=REMOTE)
        if code:
            raise RuntimeError(f"could not read Combine artifacts on {host}")
        return {"host": host, **json.loads(output)}


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(lambda host: inspect(host, args["namespace"]), args["hosts"]):
            print(json.dumps(result, separators=(",", ":")))
