"""Run single-device timings and collect per-rank TileXR records on public_fp8."""
import csv
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys

from collect import export


def load_harness(source):
    sys.path.insert(0, str(source / "tests/moonep_combine_ht"))
    import bench_send
    return bench_send


def token_test(source, binary, output, idle_timeout):
    bench = load_harness(source)
    output.mkdir()
    report = []
    with (output / "rank-times.csv").open("w", newline="") as samples:
        writer = csv.writer(samples)
        writer.writerow(("tokens", "round", "rank", "acl_us"))
        for tokens in (128, 256, 512, 1024, 2048, 4096):
            routes = output / f"route-{tokens}.u8"
            routes.write_bytes(bytes((0, 32)) * tokens + bytes((64, 96)) * (7 * tokens))
            stdout = output / f"tokens-{tokens}.log"
            command = [
                str(binary), "--mode", "reduce", "--world", "8", "--rank", "0", "--device", "0",
                "--tokens", str(tokens), "--hidden", "7168", "--topk", "2", "--experts", "256",
                "--routes", str(routes), "--chunk", str(tokens), "--splits", "1", "--reducers", "1",
                "--reduce-begin", "0", "--iterations", "25",
            ]
            print(f"waiting token={tokens} device=0", flush=True)
            bench.wait_idle([0], idle_timeout)
            with stdout.open("wb") as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=420, check=True)
            raw = bench.latency_samples(stdout, rank=0, rounds=25)
            matches = re.findall(r"reduce_tokens=(\d+) reduce_read_bytes=(\d+) partial_write_bytes=(\d+)",
                                 stdout.read_text())
            row_bytes = 7168 * 2
            assert matches == [(str(tokens), str(2 * tokens * row_bytes), str(tokens * row_bytes))] * 25
            measured = [raw[index] for index in range(5, 25)]
            writer.writerows((tokens, index, 0, value) for index, value in enumerate(measured))
            samples.flush()
            report.append({"tokens": tokens, "min_us": min(measured), "mean_us": statistics.mean(measured),
                           "max_us": max(measured), "tokens_per_us": tokens / statistics.mean(measured)})
            print(f"completed token={tokens}", flush=True)
    (output / "report.json").write_text(json.dumps({"mode": "single_rank_single_reducer",
        "tilexr_profiling": False, "warmup": 5, "rounds": 20, "contributions_per_token": 2, "cases": report}))


def profile_harness(source, output, mode, binary, idle_timeout):
    load_harness(source)
    scripts = source / "tests/moonep_combine_ht"
    patch_root = output.with_name(output.name + "-launcher")
    patch_root.mkdir()
    harness = (scripts / "bench_send.py").read_text()
    point = "            if args.routes:\n"
    assert harness.count(point) == 1
    harness = harness.replace(point, '            cmd += ["--profile", str(output / "tilexr")]\n' + point)
    (patch_root / "bench_send.py").write_text(harness)
    shutil.copyfile(scripts / "bench_gateway_reduce.py", patch_root / "bench_gateway_reduce.py")
    name = "bench_send.py" if mode == "send" else "bench_gateway_reduce.py"
    subprocess.run([
        sys.executable, "-u", str(patch_root / name), "--binary", str(binary), "--output", str(output),
        "--dispatch-ep", "64", "--combine-ep", "8", "--msprof", shutil.which("msprof"),
        "--idle-timeout", str(idle_timeout),
    ], check=True)


def internal_traces(source, output, mode):
    sdk = source / "tools/profiling/python"
    sys.path.insert(0, str(sdk))
    from tilexr_profiling.budgets import InputBudget, InputLimits
    from tilexr_profiling.records import load_record_files

    report = json.loads((output / "report.json").read_text())
    export(output)
    selected = output / "selected-traces"
    exporter = source / "tests/moonep_combine_ht/intra_send_profile.py"
    env = dict(os.environ, PYTHONPATH=str(sdk))
    total_records = dropped_records = 0
    trace_count = 0
    for choice in ("min", "max", "near_mean"):
        subset = selected / choice
        manifest = json.loads((subset / "selection.json").read_text())
        for row in manifest["ranks"]:
            rank, iteration = row["rank"], row["iteration"]
            original = (output / f"chunk-{row['chunk']}-cores-{row['cores']}" /
                        f"server-{row['server']}" / f"rank-{rank}" /
                        f"tilexr.rank{rank}.round{iteration}.records")
            target = subset / f"rank-{rank}-tilexr"
            target.mkdir()
            records = target / original.name
            shutil.copyfile(original, records)
            limits = InputLimits(max_streams=64, max_records=64 * 65536, max_memory_bytes=4 * 1024**3)
            (capture,) = load_record_files([records], budget=InputBudget(limits))
            total_records += sum(len(lane.records) for lane in capture.streams)
            dropped_records += sum(lane.dropped_count for lane in capture.streams)
            cmd = [sys.executable, str(exporter), str(records), "--cycles-per-us", "1000",
                   "--output-dir", str(target)]
            if mode == "reduce":
                cmd += ["--gateway-reduce"]
            proc = subprocess.run(cmd, env=env, capture_output=True)
            (target / "export.log").write_bytes(proc.stdout + proc.stderr)
            if proc.returncode == 0:
                trace_count += 1
        assert len(manifest["ranks"]) == 8
    report["tilexr"] = {"selected_rank_records": 24, "decoded_rank_traces": trace_count,
                        "total_marker_records": total_records, "dropped_records": dropped_records,
                        "cycles_per_us": 1000, "clock_basis": "dav-c310-vec SYS_CNT product rule"}
    (output / "report.json").write_text(json.dumps(report))
    print(json.dumps({"stage": "tilexr_trace_export", **report["tilexr"]}))


def main():
    mode, source, binary, output, timeout = sys.argv[1:]
    source, binary, output = map(Path, (source, binary, output))
    if mode == "tokens":
        token_test(source, binary, output, int(timeout))
    else:
        profile_harness(source, output, mode, binary, int(timeout))
        internal_traces(source, output, mode)


if __name__ == "__main__":
    main()
