"""Collect native timings and run the pinned Dispatch per-route analyzer."""

from collections import Counter
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

from campaign import ABC_BRANCHES, BASE, CASES, case_name, case_package
from report import MEASURED, OPERATOR, RANKS, atomic_json, copy_peer_inputs, native_rounds, rank_dir, stats


def packet_summary(report):
    tail = report["last_copyout_on_slowest_rank"]
    keys = ("producer", "consumer", "detected_us", "copyout_issued_us", "producer_drain_us",
            "recv_drain_us", "relay_flag_to_mem_submit_us", "drain_to_positive_probe_issue",
            "drain_before_positive_probe", "drain_to_previous_probe_issue",
            "other_copyouts_after_producer_drain_before_detect", "detected_to_load",
            "load_to_copyout_issue", "copyout_issue_to_drain", "relay_first_hop_packet")
    prefix = {}
    for phase in report["prefix"]:
        if phase["rank"] == report["native_slowest_rank"]:
            stage = phase["stage"]
            prefix[stage] = max(prefix.get(stage, 0), phase["duration_us"])
    tokens = report["relay_tokens"]
    role_rows = Counter()
    for row in report["routes"]:
        core = row["consumer"][1]
        role = "SEND" if core < 16 else "RECV" if core < 32 else "INTRA_SEND" if core >= 48 else "RELAY"
        if role == "RELAY":
            raise ValueError("unexpected copyout on a RELAY core")
        role_rows[role] += 1
    def token_group(group):
        return {
            "tokens": len(group),
            "posting_cores": dict(Counter(str(core) for token in group for core in token["cores"])),
            "split_tokens": sum(len(token["cores"]) > 1 for token in group),
            "other_cores_posting": dict(Counter(len(token["other_relay_cores_submitting"]) for token in group)),
        }
    return {
        "round": report["round"], "native_slowest_rank": report["native_slowest_rank"],
        "native_us": report["native_us"], "tail": {key: tail[key] for key in keys},
        "last_ready": report["last_ready_on_slowest_rank"]["detected_us"],
        "same_core_records": sum(row["consumer"] == tail["consumer"] for row in report["routes"]),
        "routes": len(report["routes"]), "copyout_rows_by_role": dict(role_rows), "slow_rank_prefix_us": prefix,
        "single_route": token_group([token for token in tokens if token["routes"] == 1]),
        "multi_route": token_group([token for token in tokens if token["routes"] > 1]),
    }


def summarize(case, peer):
    branch, _, _, mode = case
    folder = BASE / "runs" / case_name(case)
    result = json.loads((folder / "report.json").read_text())
    peer_files = folder / "peer-analysis-inputs"
    copy_peer_inputs(peer, folder, peer_files, timeout=600)
    operator = OPERATOR["dispatch", "dispatch"]
    per_rank = [native_rounds(rank_dir(folder, rank), operator) for rank in RANKS]
    per_round = {step: max(row[step] for row in per_rank) for step in MEASURED}
    result.update(native_task_us={"round_slowest_rank": stats(list(per_round.values()))},
                  native_task_round_slowest_rank_us=per_round,
                  provenance=(case_package(case) / "provenance.txt").read_text().splitlines())
    if branch in ABC_BRANCHES:
        ordered = sorted(per_round, key=lambda step: (per_round[step], step))
        result["typical_rounds"] = dict(zip(
            ("min", "median_low", "median_high", "max"), (ordered[i] for i in (0, 9, 10, 19))))
        result["native_task_us"]["rank_round"] = stats(
            [rank[step] for rank in per_rank for step in MEASURED])
    if mode == "on":
        analyzer = BASE / "trees" / branch / "tests/ep/ubx_full_intra/analyze_tail.py"
        output = folder / "tail-report"
        # The local root contains the peer directory too. Analyze that combined root once.
        with (folder / "tail-analysis.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run([sys.executable, "-B", str(analyzer), str(folder),
                                        "--output", str(output)], stdout=log, stderr=log, timeout=600)
        if completed.returncode:
            raise RuntimeError("per-route analyzer did not produce validated output")
        summaries = {}
        for path in sorted(output.glob("*-round-*.json")):
            label = path.name.split("-round-", 1)[0]
            summaries[label] = packet_summary(json.loads(path.read_text()))
        if summaries.keys() != {"max", "median_low", "median_high"}:
            raise ValueError("expected packet output for the peak and two median rounds")
        if branch in ABC_BRANCHES:
            spec = importlib.util.spec_from_file_location("analyze_tail", analyzer)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            paths = {step: [rank_dir(folder, rank) / f"rank-{rank}-round-{step}.records.bin"
                            for rank in RANKS] for step in MEASURED}
            step = result["typical_rounds"]["min"]
            minimum = module.analyze_round(paths[step], per_rank, step)
            atomic_json(output / f"min-round-{step}.json", minimum)
            summaries["min"] = packet_summary(minimum)
            result["stages"] = abc_stages(module, paths, result["typical_rounds"], folder)
        result["packet_diagnostics"] = summaries
    atomic_json(folder / "timing.json", result)


def abc_stages(module, paths, selected, folder):
    # Summarize per-round maxima of same-core stages, independent of clock alignment.
    samples = {}
    for step, rank_paths in paths.items():
        maxima, events = {}, []
        labels = [label for label, round_id in selected.items() if round_id == step]
        for rank, (path, capture) in enumerate(zip(rank_paths, module.load_record_files(rank_paths))):
            clock = path.with_suffix("").with_suffix(".clock.json")
            frequency = json.loads(clock.read_text())["cycles_per_second"] / 1e6
            decoded = module.analyze(capture, module.CORES, rank, frequency)
            if decoded["status"] != "complete":
                raise ValueError(f"failed packet trace on rank {rank} round {step}")
            origin = min(edge["tick"] for edge in decoded["edges"])
            for interval in decoded["intervals"]:
                core, stage = interval["core"], interval["stage"]
                role = ("SEND", "RECV", "RELAY", "INTRA_SEND")[core // 16]
                key = (role, stage)
                duration = interval["duration_us"]
                maxima[key] = max(maxima.get(key, 0), duration)
                if labels:
                    events.append(dict(name=stage, cat=role, ph="X", pid=rank, tid=core,
                                       ts=(interval["begin_tick"] - origin) / frequency, dur=duration))
        for key, value in maxima.items():
            samples.setdefault(key, []).append(value)
        for label in labels:
            atomic_json(folder / f"{label}-round-{step}.perfetto.json",
                        dict(traceEvents=events, round=step, time_origin="independent per rank"))
    return [dict(role=role, stage=stage, rounds=len(values), round_slowest_core_us=stats(values))
            for (role, stage), values in sorted(samples.items())]


def follow(peer):
    while True:
        reports, failures, errors = 0, {}, {}
        for case in CASES:
            name = case_name(case)
            folder = BASE / "runs" / name
            if (folder / "failure.json").is_file():
                failures[name] = json.loads((folder / "failure.json").read_text())["reason"]
            elif (folder / "report.json").is_file():
                if not (folder / "timing.json").is_file():
                    try:
                        summarize(case, peer)
                    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
                        errors[name] = str(exc)
                reports += int((folder / "timing.json").is_file())
            atomic_json(BASE / "report-status.json", {
                "reports": reports, "expected": len(CASES), "failed_cases": failures,
                "pending_report_errors": errors,
            })
        if reports + len(failures) == len(CASES):
            return
        time.sleep(30)


if __name__ == "__main__":
    follow(sys.argv[1])
