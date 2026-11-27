"""Build the local-receive candidate, benchmark it, and retain verified evidence on Green."""
import argparse
import json
from pathlib import Path, PurePosixPath
import tempfile

import benchmark
from benchmark import timings
from retest_disjoint_routes import ROOT, main

REVISION = "093dbcd714f8688697772569952c5811f846529b"
WORK = ROOT + "-local-receive-" + REVISION[:7]
TASK = "replicate4-local-receive-bs32"
SOURCE_REF = "refs/heads/codex/ubx-replicate4-disjoint-mr-retest"


def local_receive_metrics(collection):
    values = {name: [] for name in (
        "local_route_publish", "local_route_receive", "local_route_ready_from_entry",
        "first_payload_ready_from_entry", "sender_cq_wait", "local_output_drain",
    )}
    peak = {}
    latest = {}
    for rank in range(32):
        directory = collection / f"rank{rank}"
        trace = json.loads((directory / "dispatch.last.trace.json").read_text())
        metadata = trace["metadata"]
        assert metadata["receiver_schedule"] == "offloaded_local_source_v1"
        assert metadata["output_segments"] == 32 and metadata["round"] == 59
        events = trace["traceEvents"]
        starts = {e["tid"]: e["ts"] for e in events if e["name"] == "core_total" and e["ph"] == "X"}
        for event in events:
            name, core = event["name"], event.get("tid")
            if name == "local_route_publish" and event["ph"] == "X":
                values[name].append(event["dur"])
            if core not in range(16):
                continue
            if name == "local_route_receive" and event["ph"] == "X":
                values[name].append(event["dur"])
            if name == "local_route_ready" and event["ph"] == "i":
                values["local_route_ready_from_entry"].append(event["ts"] - starts[core])
            if name == "first_receiver_payload_ready" and event["ph"] == "i":
                values["first_payload_ready_from_entry"].append(event["ts"] - starts[core])
            if name == "send_completion_wait" and event["ph"] == "X":
                values["sender_cq_wait"].append(event["dur"])
            if name == "final_output_drain" and event["ph"] == "X":
                values["local_output_drain"].append(event["dur"])
        rows = json.loads((directory / "core-total-records.json").read_text())
        for row in rows[10:]:
            for core in row["cores"]:
                duration = core["end"] - core["start"]
                sample = dict(rank=rank, aiv=core["core"], round=row["round"], us=duration/1000)
                if not peak or sample["us"] > peak["us"]:
                    peak = sample
                if row["round"] == 59 and (not latest or sample["us"] > latest["us"]):
                    latest = sample
    assert len(values["local_route_publish"]) == 480
    assert len(values["local_route_receive"]) == len(values["local_route_ready_from_entry"]) == 512
    return dict(last_round={name: timings(entries) for name, entries in values.items() if len(entries)>1},
                peak_core_total=peak, last_round_peak_core_total=latest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    options = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    destination = Path(options["destination"]) / ("replicate4-local-receive-bs32-" + REVISION[:7])
    assert destination.is_absolute() and not destination.exists()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".local-receive-", dir=destination.parent) as folder:
        collection = Path(folder) / "verified"
        collection.mkdir()
        if options.get("reuse_build", False):
            benchmark.ROOT = PurePosixPath(WORK)
            benchmark.HARDWARE = benchmark.ROOT / "source/dispatch/dispatch-release/tests/hardware"
            benchmark.OUTPUT = benchmark.ROOT / "full-bs32-f50"
            benchmark.RUNTIME_INSTALL = PurePosixPath(ROOT) / "runtime-install"
            benchmark.REVISION = REVISION
            summary = benchmark.main(ports=(29500, 26500, 31000), cleanup=True, collection=collection,
                                     allow_alarm=options.get("allow_alarm", False))
        else:
            summary = main(revision=REVISION, work=WORK, ports=(29500, 26500, 31000),
                           collection=collection, source_ref=SOURCE_REF)
        summary["offloaded_local_receive"] = local_receive_metrics(collection)
        summary["local_results"] = str(destination)
        (collection / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        assert len(list(collection.glob("rank*/dispatch.last.trace.json"))) == 32
        collection.rename(destination)
    print(json.dumps(summary, separators=(",", ":")))
