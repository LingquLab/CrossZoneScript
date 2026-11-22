"""Export the full BS64/128 A/B/C phase tables for maxTokens=2."""

import json
from pathlib import Path
import sys

from dispatch_abc_setup import NAMESPACE
from pack_reports import fetch

PACK = r'''
import csv
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(sys.argv[1])
output = root / "stages-bs64-bs128-max2"
output.mkdir(exist_ok=True)
phases, operators = [], []
figures = {}
markdown = [
    "# EP32 UBX Dispatch A/B/C 阶段耗时", "",
    "提交 fe5e617d，FP8 E4M3，Hidden 3584，TopK 8，maxTokens=2。32 张 Ascend950PR。",
    "A 由 SEND 提交 TopK，RELAY 执行 fanout。B 由 RECV 提交 TopK，RELAY 执行 fanout。",
    "C 由 RECV 提交 TopK，INTRA_SEND 执行 fanout。三组均使用最后一个 payload QP。", "",
    "每项热身 5 轮，测量 20 轮。算子耗时每轮取 32 个 rank 中最慢的 msprof 任务。",
    "阶段耗时每轮取该角色内记录到的最长单次区间。表内为最小 / 中位 / 最大，单位 µs。",
    "括号内是有记录的轮数。空白表示没有该阶段的记录。阶段可重叠，阶段表使用同核区间。", "",
]
keys = ("min", "median", "max")
for batch in (64, 128):
    comparison = {}
    for variant in "ABC":
        for mode in ("off", "on"):
            case = f"tail-{variant.lower()}-dispatch-bs{batch}-max2-{mode}"
            run = root / "runs" / case
            report = json.loads((run / "timing.json").read_text())
            source = dict(line.split("=", 1) for line in report["provenance"] if "=" in line)
            if (report["case"] != case or report["samples"] != 640 or
                    report["effective_max_tokens"] != 2 or source.get("kernel_profiling") != mode):
                raise ValueError("unexpected phase report provenance")
            native = report["native_task_us"]["round_slowest_rank"]
            operator = (batch, variant, mode, *(native[key] for key in keys), report["backend"],
                        report["native"], report["abi"], report["rdma_bytes"]["median"],
                        report["rdma_GBps"]["median"])
            operators.append(operator)
            if mode == "off":
                continue
            if not report["stages"]:
                raise ValueError("missing phase timings")
            for row in report["stages"]:
                numbers = tuple(row["round_slowest_core_us"][key] for key in keys)
                identity = row["role"], row["stage"]
                rounds = row["rounds"]
                phases.append((batch, variant, *identity, rounds, *numbers))
                cell = " / ".join(f"{number:.3f}" for number in numbers)
                comparison.setdefault(identity, {})[variant] = f"{cell} ({rounds})"
                if identity in {("RECV", "topk_exchange"), ("RECV", "recv_prefix_compute"),
                                ("RELAY", "topk_exchange"), ("INTRA_SEND", "intra_send_role"),
                                ("INTRA_SEND", "topk_fanout_store")}:
                    figures.setdefault(f"{batch}/{identity[0]}/{identity[1]}", {})[variant] = round(numbers[1], 3)
    markdown += [f"## BS{batch}", "", "| 角色 | 阶段 | A | B | C |",
                 "|---|---|---:|---:|---:|"]
    for (role, phase), values in sorted(comparison.items()):
        a, b, c = (values.get(variant, "") for variant in "ABC")
        markdown.append(f"| {role} | {phase} | {a} | {b} | {c} |")
    markdown.append("")

with (output / "operator.csv").open("w", encoding="utf-8", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(("bs", "variant", "profiling", "min_us", "median_us", "max_us", "backend", "native",
                     "abi", "median_rdma_bytes", "median_rdma_GBps"))
    writer.writerows(operators)
with (output / "stages.csv").open("w", encoding="utf-8", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(("bs", "variant", "role", "stage", "rounds", "min_us", "median_us", "max_us"))
    writer.writerows(phases)
(output / "stages.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
target = output / "TileXR-UBX-ABC-BS64-BS128-max2-stages.zip"
temporary = target.with_suffix(".part")
with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
    for name in ("stages.md", "stages.csv", "operator.csv"):
        archive.write(output / name, name)
with ZipFile(temporary) as archive:
    if archive.testzip() is not None:
        raise ValueError("invalid phase table archive")
temporary.replace(target)
digest = hashlib.sha256(target.read_bytes()).hexdigest()
print(json.dumps(dict(ready=True, bundle=str(target), sha256=digest, rows=len(phases),
                      operator_rows=len(operators), figures=figures), separators=(",", ":")))
'''

if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    args.update(namespace=NAMESPACE, output_name="TileXR-UBX-ABC-BS64-BS128-max2-stages.zip")
    print(json.dumps(fetch(args, pack=PACK), separators=(",", ":")))
