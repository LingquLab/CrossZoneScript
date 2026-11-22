"""Collect and package the EP32 RELAY switch timings and representative traces."""

import json
from pathlib import Path
import sys
import time

from dispatch_relay_setup import NAMESPACE
from pack_reports import fetch

PACK = r'''
import csv
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(sys.argv[1])


def read(path):
    return json.loads(path.read_text()) if path.is_file() else {}


state = read(root / "status.json")
report = read(root / "report-status.json")
matrix = read(root / "matrix.json")
failed = report.get("failed_cases", {})
if state.get("phase") != "completed" or report.get("reports", 0) + len(failed) != len(matrix):
    print(json.dumps(dict(ready=False, queue=state, report=report), separators=(",", ":")))
    raise SystemExit(0)

files = [root / name for name in ("matrix.json", "status.json", "report-status.json")]
compat = root / "relay_cann_compat.patch"
compat_digest = hashlib.sha256(compat.read_bytes()).hexdigest() if compat.is_file() else None
if compat_digest:
    files.append(compat)
summaries = {}
baselines = {}
route_hashes = {}
trace_count = 0
stage_file = root / "stage-timings.csv"
markdown = [
    "# EP32 UBX Dispatch RELAY 开关对比", "",
    "提交 `70f1e52a`，32 张 Ascend950PR，FP8 E4M3，Hidden 3584，384 experts，TopK 8，maxTokens=2。",
    "同一提交构建全关基线和三个单开配置 `TILEXR_EP_UBX_RELAY_UB`、"
    "`TILEXR_EP_UBX_RELAY_VECTOR`、`TILEXR_EP_UBX_RELAY_TOKEN_ALIGN`。",
    "SEND 提交 TopK，RELAY 执行 fanout，TopK 使用目标 server 的最后一个 payload QP。", "",
    "每项热身 5 轮，测量 20 轮。每轮取 32 个 rank 中最慢的 msprof 算子任务。",
    "阶段统计取该角色内每轮最长的单次同核区间。Perfetto 中的时间按 rank 分别归零。",
    "算子耗时和相对基线的中位数差值均以 µs 计。", "",
    "| BS | 开关 | Profiling | 最小 | 中位 | 最大 | 中位差值 | Backend | Native | ABI | RDMA bytes 中位 | RDMA GB/s 中位 |",
    "|---:|:---|:---|---:|---:|---:|---:|:---|:---|---:|---:|---:|",
]

if compat_digest:
    markdown[7:7] = ["CANN 9.2.T101 构建兼容补丁将 PublishRelayPlan 的 UB 结构体引用改为两个原有计数字段。"
                    "补丁及 SHA-256 随报告保存。", f"`{compat_digest}`", ""]

with stage_file.open("w", encoding="utf-8", newline="") as stream:
    stages = csv.writer(stream)
    stages.writerow(("bs", "switch", "role", "phase", "rounds", "min_us", "median_us", "max_us"))
    for label, op, batch, mode, tokens in matrix:
        name = f"{label}-{op}-bs{batch}-max{tokens}-{mode}"
        variant = label.rsplit("-", 1)[-1]
        folder = root / "runs" / name
        if name in failed:
            files.append(folder / "failure.json")
            continue
        timing = read(folder / "timing.json")
        provenance = dict(line.split("=", 1) for line in timing["provenance"] if "=" in line)
        expected = {
            "revision": "70f1e52ad9b6f86cf3f72aaa97b7f734215704e5",
            "kernel_profiling": mode, "recv_topk": "OFF", "intra_fanout": "OFF",
            "relay_ub": "ON" if variant == "ub" else "OFF",
            "relay_vector": "ON" if variant == "vector" else "OFF",
            "relay_token_align": "ON" if variant == "align" else "OFF",
        }
        if not (timing["case"] == name and timing["samples"] == 640 and
                timing["native"] is True and timing["max_tokens"] == timing["effective_max_tokens"] == tokens == 2
                and all(provenance.get(key) == value for key, value in expected.items())):
            raise ValueError("invalid benchmark provenance")
        if compat_digest and variant in {"ub", "vector"} and provenance.get("source_patch_sha256") != compat_digest:
            raise ValueError("missing compile compatibility patch provenance")
        if route_hashes.setdefault(batch, timing["route_sha256"]) != timing["route_sha256"]:
            raise ValueError("route fixtures differ across builds")
        native = timing["native_task_us"]["round_slowest_rank"]
        if not 0 < native["min"] <= native["median"] <= native["max"]:
            raise ValueError("invalid timing")
        summaries[f"{batch}-{variant}-{mode}"] = {key: round(native[key], 4) for key in ("min", "median", "max")}
        if variant == "base":
            baselines[batch, mode] = native["median"]
        baseline = baselines.get((batch, mode))
        difference = f"{native['median'] - baseline:+.3f}" if baseline is not None else "—"
        files.append(folder / "timing.json")
        markdown.append(f"| {batch} | {variant} | {mode} | {native['min']:.3f} | {native['median']:.3f} | "
                        f"{native['max']:.3f} | {difference} | {timing['backend']} | {timing['native']} | {timing['abi']} | "
                        f"{timing['rdma_bytes']['median']:.0f} | {timing['rdma_GBps']['median']:.3f} |")
        if mode == "off":
            continue
        selected = timing["typical_rounds"]
        if set(selected) != {"min", "median_low", "median_high", "max"} or not timing["stages"]:
            raise ValueError("missing phase or representative rounds")
        for row in timing["stages"]:
            durations = row["round_slowest_core_us"]
            stages.writerow((batch, variant, row["role"], row["stage"], row["rounds"],
                             durations["min"], durations["median"], durations["max"]))
        for label, step in selected.items():
            trace = folder / f"{label}-round-{step}.perfetto.json"
            content = read(trace)
            if content["round"] != step or {event["pid"] for event in content["traceEvents"]} != set(range(32)):
                raise ValueError("incorrect trace rank or round coverage")
            files.extend([trace, folder / "tail-report" / f"{label}-round-{step}.json"])
            trace_count += 1

if failed:
    markdown += ["", "## 失败项", ""]
    markdown += [f"- `{case}` · {reason}" for case, reason in failed.items()]
path = root / "report.md"
path.write_text("\n".join(markdown) + "\n", encoding="utf-8")
files.extend((path, stage_file))
target = root / "TileXR-UBX-EP32-RELAY-70f1e52a.zip"
temporary = target.with_suffix(".part")
with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=4) as bundle:
    for file in files:
        bundle.write(file, file.relative_to(root))
with ZipFile(temporary) as bundle:
    if bundle.testzip() is not None:
        raise ValueError("invalid archive")
temporary.replace(target)
with target.open("rb") as stream:
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
print(json.dumps(dict(ready=True, bundle=str(target), sha256=digest, trace_count=trace_count,
                      cases=summaries, failed_cases=failed), separators=(",", ":")))
'''

if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    args.update(namespace=NAMESPACE, output_name="TileXR-UBX-EP32-RELAY-70f1e52a.zip")
    deadline = time.monotonic() + args.get("wait_seconds", 0)
    while True:
        result = fetch(args, pack=PACK, command_timeout=240)
        if (result["ready"] or time.monotonic() >= deadline
                or result.get("report", {}).get("pending_report_errors")
                or result.get("queue", {}).get("phase") in {"build_failed", "failed"}):
            break
        time.sleep(min(40, max(0, deadline - time.monotonic())))
    print(json.dumps(result, separators=(",", ":")))
