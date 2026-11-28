"""Select the slowest measured rank-round and download only the requested traces."""
import hashlib
import io
import json
import shlex
import tarfile

import benchmark


TRACE = "dispatch.last.trace.json"
REPORT = """
import json, sys
from pathlib import Path

output = Path(sys.argv[1])
receipt = json.loads((output / "host-result.json").read_text())
assert (receipt["revision"], receipt["profile_mode"], receipt["measured"]) == (sys.argv[2], "full", 50)
assert receipt["passed"] and receipt["clock_aligned"] and len(receipt["ranks"]) == 16
maxima = [0] * 50
peak = (-1, -1, -1, -1)
total = 0
for rank in receipt["ranks"]:
    rows = json.loads((output / f"rank{rank['rank']}/core-total-records.json").read_text())
    assert len(rows) == 60 and rank["verified"] and rank["verified_rounds"] == 60
    for r, row in enumerate(rows[10:], 10):
        assert (row["round"], row["rank"], row["tick_hz"]) == (r, rank["rank"], 1000000000)
        assert [core["core"] for core in row["cores"]] == list(range(56))
        for core in row["cores"]:
            ticks = core["end"] - core["start"]
            assert ticks > 0
            total += ticks
            maxima[r-10] = max(maxima[r-10], ticks)
            peak = max(peak, (ticks, rank["rank"], r, core["core"]))
print(json.dumps(dict(peak=peak, total_ticks=total, round_maxima=maxima,
                     hashes={r["rank"]: r["trace_sha256"] for r in receipt["ranks"]})))
"""

EXPORT_PEAK = """
import json, runpy, sys, tempfile
from pathlib import Path

source, exporter = map(Path, sys.argv[1:3])
rank, r, core, ticks = map(int, sys.argv[3:7])
rows = (source / "rounds.jsonl").read_text().splitlines()
row = json.loads(rows[r])
assert (row["round"], row["rank"], row["revision"]) == (r, rank, sys.argv[7])
assert row["verified"] and not row["warmup"]
with tempfile.TemporaryDirectory(prefix="ubx-peak-trace-") as folder:
    directory = Path(folder)
    (directory / "rounds.jsonl").write_text("\\n".join(rows[:r+1]) + "\\n")
    (directory / "events.bin").symlink_to(source / "events.bin")
    report = runpy.run_path(str(exporter))["export"](directory, last_only=True, no_stats=True)
    assert report["trace_round"] == r
    trace = json.loads((directory / "dispatch.last.trace.json").read_text())
    traces = {e["tid"]: e["dur"] for e in trace["traceEvents"] if e["name"] == "core_total" and e["ph"] == "X"}
    assert set(traces) == set(range(56)) and round(traces[core] * 1000) == ticks
    trace["metadata"].update(revision=row["revision"], logical_rank=rank, clock_aligned=True,
                             clock_target=row["clock_target"], route_sha256=row["route_sha256"],
                             clock_models=json.loads((source / "clock-models.json").read_text()))
    print(json.dumps(trace, separators=(",", ":")))
"""


def remote_report(host):
    command = shlex.join(["python3", "-", str(benchmark.OUTPUT), benchmark.REVISION])
    return json.loads(benchmark.ssh(host, command, payload=REPORT.encode(), stage="measured round summary"))


def download_last(host, hashes, destination, verify_trace_identity=True):
    members = {f"rank{rank}/{TRACE}": (int(rank), digest) for rank, digest in hashes.items()}
    command = shlex.join(["tar", "-cz", "-C", str(benchmark.OUTPUT), *members])
    data = benchmark.ssh(host, command, timeout=180, stage="last round traces")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as bundle:
        entries = bundle.getmembers()
        assert len(entries) == len(members) and {e.name for e in entries} == set(members)
        for entry in entries:
            assert entry.isfile()
            rank, expected_sha = members[entry.name]
            raw = bundle.extractfile(entry).read()
            if verify_trace_identity:
                assert hashlib.sha256(raw).hexdigest() == expected_sha
                metadata = json.loads(raw)["metadata"]
                assert (metadata["logical_rank"], metadata["round"], metadata["revision"]) == (rank, 59, benchmark.REVISION)
            path = destination / entry.name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(raw)


def collect(pool, destination, bs=32, verify_trace_identity=True):
    reports = list(pool.map(remote_report, benchmark.HOSTS))
    for index, report in enumerate(reports):
        assert set(map(int, report["hashes"])) == set(range(16*index, 16*(index+1)))
    list(pool.map(lambda item: download_last(item[0], item[1]["hashes"], destination, verify_trace_identity),
                  zip(benchmark.HOSTS, reports)))
    ticks, rank, round_id, core = max(tuple(report["peak"]) for report in reports)
    filename = TRACE if round_id == 59 else f"dispatch.round{round_id}.trace.json"
    peak_path = f"rank{rank}/{filename}"
    if round_id != 59:
        command = shlex.join(["python3", "-", str(benchmark.OUTPUT / f"rank{rank}"),
                             str(benchmark.HARDWARE / "export_dispatch_trace.py"),
                             str(rank), str(round_id), str(core), str(ticks), benchmark.REVISION])
        data = benchmark.ssh(benchmark.HOSTS[rank//16], command, payload=EXPORT_PEAK.encode(),
                             timeout=150, stage="slowest measured round trace")
        if verify_trace_identity:
            metadata = json.loads(data)["metadata"]
            assert (metadata["logical_rank"], metadata["round"], metadata["revision"]) == (rank, round_id, benchmark.REVISION)
        (destination / peak_path).write_bytes(data)
    assert len(list(destination.rglob("*.trace.json"))) == 32 + int(round_id != 59)
    maxima = [max(pair)/1000 for pair in zip(*(report["round_maxima"] for report in reports))]
    result = dict(passed=True, revision=benchmark.REVISION, bs=bs, warmup=10, measured=50, verified_ranks=32,
                clock_aligned=True, last_trace_round=59, last_round_files=32,
                slowest=dict(rank=rank, round=round_id, aiv=core, us=ticks/1000, trace=peak_path),
                core_mean_us=sum(report["total_ticks"] for report in reports)/(32*50*56*1000),
                round_core_maxima_stats=benchmark.timings(maxima))
    if verify_trace_identity:
        result["slowest_trace_sha256"] = hashlib.sha256((destination / peak_path).read_bytes()).hexdigest()
    return result
