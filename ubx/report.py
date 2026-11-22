"""Collect EP32 result metadata in the test zone and export selected Perfetto traces."""

from collections import defaultdict
import csv
import json
import os
from pathlib import Path
import re
from statistics import median
import subprocess
import sys
import tarfile
import time

from campaign import BASE, CASES, case_name, case_package, process

COMBINE = ("run", "init", "scatter", "scatter_ready", "prepare", "submit",
           "remote_reduce", "contributions", "progress", "readiness_tail",
           "final_reduce", "drain", "publish")
OPERATOR = {
    ("dispatch", "dispatch"): "tilexr_ep_ubx_kernel",
    ("stages", "write"): "ubx_combine_write",
    ("stages", "read"): "tilexr_ep_ubx_combine_kernel",
    ("sleep", "write"): "tilexr_ep_ubx_combine_write_kernel",
}
RANKS = range(32)
MEASURED = range(5, 25)


def stats(values, prefix=""):
    return {prefix + "min": min(values), prefix + "median": median(values), prefix + "max": max(values)}


def atomic_json(path, content):
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def copy_peer_inputs(peer, source, destination, timeout=180):
    if (destination / ".ready").is_file():
        return
    destination.mkdir(exist_ok=True)
    command = [
        "/bin/bash", "-c",
        'set -eo pipefail; cd "$1"; find rank-* -type f \\( -name "*.records.bin" -o -name "*.clock.json" -o -iname "op_summary*.csv" \\) -print0 | tar --null -T - -cf -',
        "outputs", str(source),
    ]
    archive = destination / "inputs.tar"
    with archive.open("wb") as output:
        subprocess.run(process(peer, command), stdout=output, stderr=subprocess.PIPE,
                       check=True, timeout=timeout)
    with tarfile.open(archive) as data:
        for member in data:
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts or not member.isfile():
                raise ValueError("unexpected path in result archive")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data.extractfile(member).read())
    archive.unlink()
    (destination / ".ready").touch()


def rank_dir(case_dir, rank):
    root = case_dir if rank < 16 else case_dir / "peer-analysis-inputs"
    return root / f"rank-{rank:02}"


def native_rounds(rank_folder, operator):
    rows = set()
    for file in rank_folder.rglob("*.csv"):
        if not file.name.lower().startswith("op_summary"):
            continue
        with file.open(encoding="utf-8-sig", newline="") as handle:
            for values in csv.DictReader(handle):
                row = {re.sub(r"[^a-z0-9]", "", (k or "").lower()): v for k, v in values.items()}
                if row.get("optype") != operator and row.get("opname") != operator:
                    continue
                duration = float(row["taskdurationus"])
                start = float(row["taskstarttimeus"])
                rows.add((start, duration))
    items = [duration for _, duration in sorted(rows)]
    if len(items) != 25:
        raise ValueError(f"native profiler {rank_folder.name}: {len(items)} of 25 {operator} tasks")
    return items


def capture_mapping(case, cycles_per_us):
    branch, op, _, _ = case[:4]
    if branch == "dispatch":
        document = (BASE / "trees" / "dispatch" / "docs" / "ep-ubx-profiling.md").read_text(encoding="utf-8")
        mapping = json.loads(document.split("```json\n", 1)[1].split("```", 1)[0])
        for stage in mapping["stages"]:
            if stage["name"] == "result_init":
                stage.pop("parent", None)
    else:
        mapping = {"schema": "tilexr_profiling_mapping.v1", "operator": OPERATOR[branch, op],
                   "stages": [{"name": name, "category": "compute" if index else "total", "critical": index == 0,
                               "begin": 2 * index + 1, "end": 2 * index + 2}
                              for index, name in enumerate(COMBINE)]}
    mapping["cycles_per_us"] = cycles_per_us
    return mapping


def captures(case_dir):
    by_round = defaultdict(dict)
    for rank in RANKS:
        for path in rank_dir(case_dir, rank).glob("*.records.bin"):
            match = re.search(r"-round-(\d+)\.records\.bin$", path.name)
            if match:
                by_round[int(match[1])][rank] = path
    if set(by_round) != set(range(25)) or any(set(paths) != set(RANKS) for paths in by_round.values()):
        raise ValueError("profile lacks a rank or round")
    return by_round


def stage_role(case, core):
    branch, op, _, _ = case[:4]
    if branch == "dispatch":
        return ("SEND", "RECV", "RELAY", "INTRA_SEND")[min(core // 16, 3)]
    if op == "read":
        return "SEND" if core < 16 else "RELAY"
    return "CONTRIBUTE" if core >= 28 else ("SEND_REDUCE" if core < 16 else "REDUCE")


def profiler_frequency(case_dir, case):
    branch = case[0]
    source = case_dir
    if branch == "stages":
        previous = BASE / "prior" if (BASE / "prior").is_dir() else BASE
        source = previous / "runs" / "dispatch-dispatch-bs64-on"
    nodes = (source, source / "peer-analysis-inputs")
    values = set()
    for index, root in enumerate(nodes):
        folder = root / f"rank-{16 * index:02}"
        model = next(folder.glob("*.clock.json"), None)
        if model is not None:
            values.add(json.loads(model.read_text(encoding="utf-8"))["cycles_per_second"])
    if len(values) != 1 or not values:
        raise ValueError("matching CANN clock frequency unavailable")
    return values.pop() / 1e6


def make_trace(path, case, role_samples, origins, frequency, round_id):
    events = []
    for lane, sample in role_samples:
        events.append({"name": sample.name, "cat": stage_role(case, lane.core), "ph": "X",
                       "pid": lane.rank, "tid": lane.core,
                       "ts": (sample.raw_start - origins[lane.rank]) / frequency,
                       "dur": sample.duration_us})
    atomic_json(path, {"traceEvents": events, "displayTimeUnit": "ns",
                       "round": round_id, "time_origin": "independent earliest RunBegin per rank"})


def analyze_stages(case_dir, case, selected):
    from tilexr_profiling.pairing import load_profile_run

    frequency = profiler_frequency(case_dir, case)
    mapping = capture_mapping(case, frequency)
    files = captures(case_dir)
    groups = defaultdict(list)
    round_max = defaultdict(dict)
    for round_id in MEASURED:
        run = load_profile_run(mapping, list(files[round_id].values()))
        if run.status != "complete" or len(run.launches) != 1:
            raise ValueError(f"incomplete profile in round {round_id}")
        lanes = [lane for lane in run.launches[0].lanes if lane.core < 56]
        if {(lane.rank, lane.core) for lane in lanes} != {(rank, core) for rank in RANKS for core in range(56)}:
            raise ValueError(f"incomplete physical lanes in round {round_id}")
        spans = [(lane, sample) for lane in lanes for sample in lane.samples if sample.kind == "interval"]
        origins = {rank: min(sample.raw_start for lane, sample in spans
                             if lane.rank == rank and sample.critical) for rank in RANKS}
        for lane, sample in spans:
            key = (stage_role(case, lane.core), sample.name)
            groups[key].append(sample.duration_us)
            if round_id in round_max[key]:
                round_max[key][round_id] = max(round_max[key][round_id], sample.duration_us)
            else:
                round_max[key][round_id] = sample.duration_us
        for kind, chosen in selected.items():
            if round_id == chosen:
                make_trace(case_dir / f"{kind}-round-{round_id}.perfetto.json",
                           case, spans, origins, frequency, round_id)
    return [{"role": role, "stage": stage, "core_round_samples": len(values),
             "core_round_us": stats(values), "rounds": len(round_max[role, stage]),
             "round_slowest_core_us": stats(list(round_max[role, stage].values()))}
            for (role, stage), values in sorted(groups.items())]


def report_case(case, peer):
    case_dir = BASE / "runs" / case_name(case)
    report = json.loads((case_dir / "report.json").read_text(encoding="utf-8"))
    copy_peer_inputs(peer, case_dir, case_dir / "peer-analysis-inputs")
    op = OPERATOR[case[:2]]
    native = {rank: native_rounds(rank_dir(case_dir, rank), op) for rank in RANKS}
    tail = {round_id: max(native[rank][round_id] for rank in RANKS) for round_id in MEASURED}
    ordered = sorted(tail, key=lambda round_id: (tail[round_id], round_id))
    report["native_task_us"] = {"rank_round": stats([native[r][i] for r in RANKS for i in MEASURED]),
                                "round_slowest_rank": stats(list(tail.values()))}
    report["native_task_round_slowest_rank_us"] = tail
    report["typical_rounds"] = dict(zip(("min", "middle_low", "middle_high", "max"),
                                        [ordered[i] for i in (0, 9, 10, 19)]))
    info = case_package(case) / "provenance.txt"
    report["provenance"] = info.read_text().splitlines()
    report["capture_mode"] = {"kernel_profiling": case[3], "native_profiler": True, "stage_barrier": True}
    if case[3] == "on":
        report["stages"] = analyze_stages(case_dir, case, report["typical_rounds"])
    atomic_json(case_dir / "timing.json", report)


def write_markdown(reports, failures):
    lines = ["# UBX EP32 性能报告", "",
             "两台主机共 32 rank，H3584。每种配置预热 5 轮，以下统计后续 20 轮。",
             "时延统计先取每轮最慢 rank，再取 20 轮的最小值、中位数和最大值。", "",
             "| 用例 | Backend | Native | ABI | 算子最小 µs | 中位 µs | 最大 µs | RDMA 中位字节 | 中位 GB/s |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for report in reports:
        native = report["native_task_us"]["round_slowest_rank"]
        lines.append(f"| {report['case']} | {report['backend']} | {report['native']} | {report['abi']} "
                     f"| {native['min']:.3f} | {native['median']:.3f} | {native['max']:.3f} "
                     f"| {report['rdma_bytes']['median']:.0f} | {report['rdma_GBps']['median']:.3f} |")
    if (BASE / "matrix.json").is_file():
        lines.extend(["", "## RDMA 聚包配置", "",
                      "max1 和 max2 分别表示每个 RDMA 包最多包含 1 和 2 个 token。"])
        if CASES[0][0] == "dispatch":
            if any(report.get("reused_from") for report in reports):
                lines.append("沿用上轮 FP8 Dispatch max2 的 4 个完整结果。max1 为本轮实测。")
            lines.append("Dispatch 用例使用相同形状、路由、sleep_at、框架 profiler 和生产屏障。")
        else:
            lines.extend(["沿用上轮 stages-1-7 Combine 读 max2 的 6 个完整结果，其余项目本轮实测。",
                          "stages-1-7 用例关闭 sleep_at。第 6、10、14、18、22 轮为空路由轮，最小值可能来自这些轮次。",
                          "sleep-tests 使用夹具自带的 sleep_at。两种模式的框架 profiler 和屏障均开启。"])
    if failures:
        lines.extend(["", "## 未通过的用例", ""])
        lines.extend(f"- {case}。{reason}" for case, reason in failures.items())
    for report in reports:
        if "stages" not in report:
            continue
        case = report["case"]
        lines.extend(["", "## " + case, "",
                      "| 核角色 | 阶段 | 测量轮数 | 最小 µs | 中位 µs | 最大 µs |",
                      "|---|---|---:|---:|---:|---:|"])
        for stage in report["stages"]:
            v = stage["round_slowest_core_us"]
            lines.append(f"| {stage['role']} | {stage['stage']} | {stage['rounds']} "
                         f"| {v['min']:.3f} | {v['median']:.3f} | {v['max']:.3f} |")
        lines.append("")
        lines.extend(f"- [{label}，第 {i} 轮](runs/{case}/{label}-round-{i}.perfetto.json)"
                     for label, i in report["typical_rounds"].items())
    lines.extend(["", "阶段统计取每轮执行该阶段的最慢核。各核并行，阶段耗时可以重叠。",
                  "Trace 按每个 rank 的 RunBegin 分别对齐。RDMA 带宽分母为该条记录的流事件区间。",
                  "每个用例的 timing.json 保留源码、CANN、样本数、每轮 Native Task 和逐阶段数据。"])
    target = BASE / "report.md"
    temporary = target.with_suffix(".part")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(target)


def follow(peer):
    sys.path.insert(0, str(BASE / "trees" / "dispatch" / "tools" / "profiling" / "python"))
    previous = None
    while True:
        reports, errors, failures = [], {}, {}
        for case in CASES:
            directory = BASE / "runs" / case_name(case)
            failure = directory / "failure.json"
            if failure.is_file():
                try:
                    failures[case_name(case)] = json.loads(failure.read_text(encoding="utf-8"))["reason"]
                except json.JSONDecodeError:
                    continue
            if (directory / "report.json").is_file() and not (directory / "timing.json").is_file():
                try:
                    report_case(case, peer)
                except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
                    errors[case_name(case)] = str(exc)
            if (directory / "timing.json").is_file():
                reports.append(json.loads((directory / "timing.json").read_text(encoding="utf-8")))
        state = {"reports": len(reports), "expected": len(CASES), "failed_cases": failures,
                 "pending_report_errors": errors}
        if state != previous:
            write_markdown(reports, failures)
            atomic_json(BASE / "report-status.json", state)
            previous = state
        if len(reports) + len(failures) == len(CASES):
            return
        time.sleep(45)


if __name__ == "__main__":
    follow(sys.argv[1])
