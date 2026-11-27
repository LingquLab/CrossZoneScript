"""Benchmark the pinned 24-worker candidate and preserve verified traces on Green."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile

import benchmark
from build import HOSTS, ROOT, TILEXR, archive, pin
from local_receive_timeout import compact, inspect
from retest_disjoint_routes import build_dispatch
from retest_local_receive import REVISION as BASELINE, WORK as BASELINE_WORK

REVISION = "cb5bb26a0305365715c25fe7333aafa87c15bb6e"
SOURCE_REF = "refs/heads/UBX-dispatch-route-replicate4"
WORK = ROOT + "-relay24-" + REVISION[:7]


def review_traces(collection):
    samples = {name: [] for name in (
        "local_sender_completion_wait", "relay_route_ready_from_entry",
        "relay_send_completion_wait", "relay_done_from_entry",
    )}
    last_peak = dict(us=0)
    for rank in range(32):
        trace = json.loads((collection / f"rank{rank}/dispatch.last.trace.json").read_text())
        assert trace["metadata"]["round"] == 59
        windows = {}
        for event in trace["traceEvents"]:
            if event.get("ph") == "X":
                windows.setdefault((event["tid"], event["name"]), []).append(event)
                if event["name"] == "core_total" and event["dur"] > last_peak["us"]:
                    last_peak = dict(rank=rank, aiv=event["tid"], us=event["dur"])
        for core in range(16, 24):
            total, = windows[core, "core_total"]
            local, = windows[core, "local_sender_completion_wait"]
            route, = windows[core, "route_sync"]
            relay, = windows[core, "send_completion_wait"]
            assert local["ts"] + local["dur"] <= route["ts"] + 1e-6
            assert route["ts"] + route["dur"] <= relay["ts"] + 1e-6
            samples["local_sender_completion_wait"].append(local["dur"])
            samples["relay_route_ready_from_entry"].append(route["ts"] + route["dur"] - total["ts"])
            samples["relay_send_completion_wait"].append(relay["dur"])
            samples["relay_done_from_entry"].append(relay["ts"] + relay["dur"] - total["ts"])
    return dict(hybrid_aiv_last_round={key: benchmark.timings(values) for key, values in samples.items()},
                last_round_peak_core_total=last_peak)


def build_candidate():
    script = Path(__file__).with_name("build_disjoint_routes.sh").read_bytes().replace(b"\r\n", b"\n")
    with tempfile.TemporaryDirectory(prefix="ubx-relay24-", dir=Path.cwd()) as staging:
        directory = Path(staging).resolve()
        repo = directory / "tilexr"
        pin(repo, TILEXR, (REVISION,), source_ref=SOURCE_REF)
        source = archive(directory, repo, "dispatch-relay24", REVISION, "dispatch/dispatch-release")
        sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        with ThreadPoolExecutor(max_workers=2) as pool:
            builds = list(pool.map(lambda host: build_dispatch(host, source, sha256, script, WORK, REVISION), HOSTS))
        assert builds[0] == builds[1]


def run_benchmark(destination, work, revision, run_name, label):
    result_dir = destination / label
    assert destination.is_absolute() and destination.is_dir()
    assert not result_dir.exists()
    benchmark.ROOT = PurePosixPath(work)
    benchmark.HARDWARE = benchmark.ROOT / "source/dispatch/dispatch-release/tests/hardware"
    benchmark.OUTPUT = benchmark.ROOT / run_name
    benchmark.RUNTIME_INSTALL = PurePosixPath(ROOT) / "runtime-install"
    benchmark.REVISION = revision
    with tempfile.TemporaryDirectory(prefix=".relay24-", dir=destination) as staging:
        collection = Path(staging) / "verified"
        collection.mkdir()
        summary = benchmark.main(ports=(31500, 25000, 27000), collection=collection)
        if revision == REVISION:
            summary.update(review_traces(collection), relay_workers=24)
        summary["local_results"] = str(result_dir)
        (collection / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        assert len(list(collection.glob("rank*/dispatch.last.trace.json"))) == 32
        collection.rename(result_dir)
    print(json.dumps(summary, separators=(",", ":")))


def save_failure_evidence(destination, work, run_name):
    with ThreadPoolExecutor(max_workers=2) as pool:
        hosts = list(pool.map(lambda host: inspect(host, work, run_name), HOSTS))
    result_dir = destination / ("replicate4-relay24-bs32-" + REVISION[:7] + "-diagnostics")
    result_dir.mkdir(exist_ok=True)
    report = result_dir / (run_name + "-warmup-evidence.json")
    report.write_text(json.dumps(hosts, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(local_diagnostics=str(report), hosts=list(map(compact, hosts))), separators=(",", ":")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    options = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    destination = Path(options["destination"])
    mode = options.get("mode", "initial")
    candidate_label = "replicate4-relay24-bs32-" + REVISION[:7]
    if mode == "initial":
        build_candidate()
        run_benchmark(destination, WORK, REVISION, "full-bs32-f50", candidate_label)
    elif mode == "control":
        run_benchmark(destination, BASELINE_WORK, BASELINE, "full-bs32-f50-relay24-control",
                      "replicate4-relay16-bs32-" + BASELINE[:7] + "-control")
    elif mode == "retry":
        run_benchmark(destination, WORK, REVISION, "full-bs32-f50-relay24-retry", candidate_label)
    elif mode == "diagnostics":
        save_failure_evidence(destination, WORK, "full-bs32-f50")
    elif mode == "control_diagnostics":
        save_failure_evidence(destination, BASELINE_WORK, "full-bs32-f50-relay24-control")
    else:
        raise ValueError("unknown benchmark mode")
