"""Export replicate4 rank 10 round 54 from the retained hardware profile."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import runpy
import shlex
import tarfile
import tempfile

from benchmark import HOSTS, ssh
from retest_disjoint_routes import REVISION, WORK

RANK = 10
ROUND = 54
RANK_PATH = f"full-bs32-f50/rank{RANK}"
EXPORTER = "source/dispatch/dispatch-release/tests/hardware/export_dispatch_trace.py"
EXPORTER_SHA256 = "3453eefb8a9d2688f4406f795f5195c4cfd37167ab0fc8f85f3e9c8312b90c88"


def export(destination):
    rank_records = ("rounds.jsonl", "events.bin", "clock-models.json")
    members = {f"{RANK_PATH}/{name}": name for name in rank_records}
    members[EXPORTER] = "export_dispatch_trace.py"
    command = shlex.join(["tar", "-cz", "-C", str(PurePosixPath(WORK)), *members])
    raw = ssh(HOSTS[0], command, timeout=120, stage="Read replicate4 rank 10 saved profile")
    with tempfile.TemporaryDirectory(prefix="ubx-round54-") as folder:
        staging = Path(folder)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            assert sorted(archive.getnames()) == sorted(members)
            for entry in archive.getmembers():
                assert entry.isfile()
                (staging / members[entry.name]).write_bytes(archive.extractfile(entry).read())
        exporter = staging / "export_dispatch_trace.py"
        assert hashlib.sha256(exporter.read_bytes()).hexdigest() == EXPORTER_SHA256
        rows = (staging / "rounds.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(rows) == 60
        row = json.loads(rows[ROUND])
        assert (row["rank"], row["round"], row["revision"]) == (RANK, ROUND, REVISION)
        assert row["profile_mode"] == "full" and row["bs"] == 32 and row["tick_hz"] == 1000000000
        assert len(row["cores"]) == 56 and not row["relay_watch_detail_enabled"] and not row["route_sync_detail_enabled"]
        # Select the original round as the exporter's last row, preserving event offsets.
        (staging / "rounds.jsonl").write_text("\n".join(rows[:ROUND + 1]) + "\n", encoding="utf-8")
        report = runpy.run_path(str(exporter))["export"](staging, last_only=True, no_stats=True)
        assert report["trace_round"] == ROUND
        trace = json.loads((staging / "dispatch.last.trace.json").read_text(encoding="utf-8"))
        trace["metadata"].update(
            revision=REVISION, logical_rank=RANK, ep_size=32, active_ranks=list(range(32)),
            route_sha256=row["route_sha256"], clock_aligned=True, clock_target=row["clock_target"],
            clock_models=json.loads((staging / "clock-models.json").read_text(encoding="utf-8")),
            route_sync_detail_enabled=False, relay_watch_detail_enabled=False,
        )
        totals = {event["tid"]: event["dur"] for event in trace["traceEvents"]
                  if event.get("ph") == "X" and event["name"] == "core_total"}
        assert set(totals) == set(range(56)) and round(totals[36], 3) == 43.616
        output = json.dumps(trace, separators=(",", ":")).encode("utf-8")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".round54-", dir=destination.parent) as folder:
        staged_output = Path(folder) / destination.name
        staged_output.write_bytes(output)
        if destination.exists():
            assert destination.read_bytes() == output
        else:
            staged_output.rename(destination)
    assert destination.read_bytes() == output
    print(json.dumps(dict(path=str(destination), rank=RANK, round=ROUND, events=len(trace["traceEvents"]),
                          core_total_us=totals[36], route_sync_detail_enabled=False,
                          relay_watch_detail_enabled=False, sha256=hashlib.sha256(output).hexdigest())))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    options = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    destination = Path(options["destination"])
    assert destination.is_absolute()
    export(destination / "replicate4" / f"rank{RANK}" / f"dispatch.round{ROUND}.trace.json")
