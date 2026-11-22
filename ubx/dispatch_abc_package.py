"""Build the Dispatch TopK A/B/C comparison and download the verified archive."""

import json
from pathlib import Path
import sys
import time

from dispatch_abc_setup import NAMESPACE
from pack_reports import fetch

PACK = r'''
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(sys.argv[1])


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_optional(path):
    return read(path) if path.is_file() else {}


queue = read_optional(root / "status.json")
report = read_optional(root / "report-status.json")
matrix = read(root / "matrix.json")
failures = report.get("failed_cases", {})
if queue.get("phase") != "completed" or report.get("reports", 0) + len(failures) != len(matrix):
    batches = {}
    for label, op, batch, mode, tokens in matrix:
        if batch != 64:
            continue
        entry = root / "runs" / f"{label}-{op}-bs{batch}-max{tokens}-{mode}" / "timing.json"
        timing = read_optional(entry)
        if timing:
            batches[f"{batch}-max{tokens}-{label[-1].upper()}-{mode}"] = (
                timing["native_task_us"]["round_slowest_rank"])
    print(json.dumps(dict(ready=False, queue=queue, report=report, bs64=batches), separators=(",", ":")))
    raise SystemExit(0)

files = [root / name for name in ("matrix.json", "status.json", "report-status.json")]
summaries = {}
by_batch_mode = {}
trace_count = 0
markdown = [
    "# EP32 UBX Dispatch TopK A/B/C", "",
    "共 32 张 Ascend950PR，FP8 E4M3，Hidden 3584，384 experts，TopK 8，maxTokens 1 和 2。",
    "两台服务器使用提交 fe5e617d。A 由 SEND 提交 TopK，RELAY 执行 fanout。",
    "B 由 RECV 提交 TopK，RELAY 执行 fanout。C 由 RECV 提交 TopK，INTRA_SEND 执行 fanout。",
    "三个配置的 TopK 均由目标 server 的最后一个 payload QP 提交。", "",
    "每项先热身 5 轮，再测量 20 轮。每轮取 32 个 rank 中最慢的 msprof 算子耗时。",
    "Profiling on 的阶段统计按角色取每轮最慢的核，保存最小轮、两个中位轮和最大轮的 trace。",
    "Perfetto 时间按 rank 独立归零。逐路由分析另有时钟校准及误差界。", "",
    "| BS | Max tokens | 配置 | Profiling | 最小 µs | 中位 µs | 最大 µs | Backend | Native | ABI | RDMA 中位 bytes | RDMA 中位 GB/s |",
    "|---:|---:|:---|:---|---:|---:|---:|:---|:---|---:|---:|---:|",
]
for label, op, batch, mode, tokens in matrix:
    case = f"{label}-{op}-bs{batch}-max{tokens}-{mode}"
    directory = root / "runs" / case
    if case in failures:
        files.append(directory / "failure.json")
        continue
    item = read(directory / "timing.json")
    source = dict(line.split("=", 1) for line in item["provenance"] if "=" in line)
    recv = "OFF" if label == "tail-a" else "ON"
    intra = "ON" if label == "tail-c" else "OFF"
    if not (item["samples"] == 32 * 20 and item["native"] and item["max_tokens"] == item["effective_max_tokens"] == tokens
            and source["revision"] == "fe5e617d54f5735fb08c730aa71e71a76c5e6988"
            and source["kernel_profiling"] == mode and source["recv_topk"] == recv
            and source["intra_fanout"] == intra):
        raise ValueError("unexpected config or incomplete sampling")
    native = item["native_task_us"]["round_slowest_rank"]
    name = label[-1].upper()
    summaries[f"{batch}-max{tokens}-{name}-{mode}"] = native
    by_batch_mode.setdefault((batch, tokens, mode), {})[name] = native["median"]
    files.append(directory / "timing.json")
    markdown.append(f"| {batch} | {tokens} | {name} | {mode} | {native['min']:.3f} | {native['median']:.3f} | "
                    f"{native['max']:.3f} | {item['backend']} | {item['native']} | {item['abi']} | "
                    f"{item['rdma_bytes']['median']:.0f} | {item['rdma_GBps']['median']:.3f} |")
    if mode == "on":
        selected = item["typical_rounds"]
        if set(selected) != {"min", "median_low", "median_high", "max"} or not item["stages"]:
            raise ValueError("missing representative rounds or stage data")
        for mark, step in selected.items():
            trace = directory / f"{mark}-round-{step}.perfetto.json"
            content = read(trace)
            if content["round"] != step or {event["pid"] for event in content["traceEvents"]} != set(range(32)):
                raise ValueError("incorrect trace rank/round coverage")
            files.extend([trace, directory / "tail-report" / f"{mark}-round-{step}.json"])
            trace_count += 1

markdown += ["", "## 变更前后中位耗时", "",
             "差值为后一个配置减去前一个配置，单位 µs。", "",
             "| BS | Max tokens | Profiling | B − A | C − B | C − A |",
             "|---:|---:|:---|---:|---:|---:|"]
for (batch, tokens, mode), pair in by_batch_mode.items():
    def delta(first, second):
        return f"{pair[second] - pair[first]:+.3f}" if first in pair and second in pair else "—"

    markdown.append(f"| {batch} | {tokens} | {mode} | {delta('A', 'B')} | {delta('B', 'C')} | {delta('A', 'C')} |")
if failures:
    markdown += ["", "## 未完成项", ""]
    markdown += [f"- `{case}` · {reason}" for case, reason in failures.items()]
document = root / "report.md"
document.write_text("\n".join(markdown) + "\n", encoding="utf-8")
files.append(document)

target = root / "TileXR-UBX-Dispatch-TopK-ABC.zip"
temporary = target.with_suffix(".part")
with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=4) as bundle:
    for path in files:
        bundle.write(path, path.relative_to(root))
with ZipFile(temporary) as bundle:
    if bundle.testzip() is not None:
        raise ValueError("invalid archive")
temporary.replace(target)
with target.open("rb") as stream:
    sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
print(json.dumps(dict(ready=True, bundle=str(target), sha256=sha256, trace_count=trace_count,
                      cases=summaries, failed_cases=failures), separators=(",", ":")))
'''

if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    args["namespace"] = NAMESPACE
    deadline = time.monotonic() + args.get("wait_seconds", 0)
    try:
        while True:
            result = fetch(args, pack=PACK, command_timeout=240)
            if (result["ready"] or time.monotonic() >= deadline
                    or result.get("report", {}).get("pending_report_errors")):
                break
            time.sleep(min(40, max(0, deadline - time.monotonic())))
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps(dict(ready=False, error=type(error).__name__)))
        raise SystemExit(1) from None
    print(json.dumps(result, separators=(",", ":")))
