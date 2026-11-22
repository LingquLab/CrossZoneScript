"""Compare the paired EP32 Dispatch runs and deliver the report in the green zone."""

from hashlib import file_digest
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import time

import paramiko

from stage_sources import connect, run_remote


COMPARE = r'''
import csv
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

siblings, names = Path(sys.argv[1]), sys.argv[2:]
roots = [siblings / name for name in names]
cases = [(batch, cap, mode) for batch in (64, 128)
         for cap in (1, 2) for mode in ("off", "on")]
expected_revisions = (
    "fa0eaf8a8139294d030c9fbaed44b275037367ba",
    "e4a9e4c2a313c2def736404cb81d42193e72e336",
)

def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}

def state(root):
    queue = read(root / "status.json")
    report = read(root / "report-status.json")
    build_file = root / "build.exit"
    build = int(build_file.read_text()) if build_file.is_file() else None
    return {"phase": queue.get("phase"), "case": queue.get("case"),
            "reports": report.get("reports", 0), "failed": report.get("failed_cases", {}),
            "pending_report_errors": report.get("pending_report_errors", {}), "build": build}

states = [state(root) for root in roots]
settled = all(row["phase"] == "completed" and row["reports"] + len(row["failed"]) == len(cases)
              for row in states)
if not settled:
    print(json.dumps({"ready": False, "states": states}))
    raise SystemExit(0)

rows = []
phases = []
files = []
for batch, cap, mode in cases:
    case = f"dispatch-dispatch-bs{batch}-max{cap}-{mode}"
    pair = []
    for root, version, status in zip(roots, expected_revisions, states):
        path = root / "runs" / case / "timing.json"
        row = read(path)
        if row:
            if (row["max_tokens"] != cap or row["effective_max_tokens"] != cap or
                    row["samples"] != 640 or not row["native"] or
                    "revision=" + version not in row["provenance"]):
                raise ValueError(f"{root.name}/{case} has inconsistent cap, provenance, or coverage")
            files.append((path, root.name + "/runs/" + case + "/timing.json"))
            if mode == "on":
                for kind, round_id in row["typical_rounds"].items():
                    filename = f"{kind}-round-{round_id}.perfetto.json"
                    trace = path.with_name(filename)
                    if len({item["pid"] for item in read(trace)["traceEvents"]}) != 32:
                        raise ValueError("incomplete 32-rank trace")
                    files.append((trace, root.name + "/runs/" + case + "/" + filename))
        elif case not in status["failed"]:
            raise ValueError(f"{root.name}/{case} lacks a recorded outcome")
        pair.append(row)
    base, current = pair
    result = {"bs": batch, "max_tokens": cap, "mode": mode}
    for tag, entry in zip(("fa0eaf8a", "e4a9e4c2"), pair):
        if entry:
            native = entry["native_task_us"]["round_slowest_rank"]
            result.update({tag + "_" + key + "_us": native[key] for key in ("min", "median", "max")})
            result[tag + "_rdma_bytes_median"] = entry["rdma_bytes"]["median"]
            result[tag + "_rdma_GBps_median"] = entry["rdma_GBps"]["median"]
            result[tag + "_backend"] = entry["backend"]
            result[tag + "_abi"] = entry["abi"]
            result[tag + "_native"] = entry["native"]
        else:
            result[tag + "_status"] = "failed"
    if base and current:
        a = result["fa0eaf8a_median_us"]
        b = result["e4a9e4c2_median_us"]
        result["e4_vs_fa_median_pct"] = (b / a - 1) * 100
        metrics = [{(stage["role"], stage["stage"]): stage for stage in item.get("stages", [])}
                   for item in pair]
        for role, stage in sorted(metrics[0].keys() & metrics[1].keys()):
            first, second = [table[role, stage]["round_slowest_core_us"] for table in metrics]
            phases.append({"bs": batch, "max_tokens": cap, "mode": mode,
                           "role": role, "stage": stage,
                           **{"fa0eaf8a_" + k + "_us": first[k] for k in ("min", "median", "max")},
                           **{"e4a9e4c2_" + k + "_us": second[k] for k in ("min", "median", "max")}})
    rows.append(result)

output = roots[0] / "comparison"
output.mkdir(exist_ok=True)
md = ["# UBX EP32 FP8 Dispatch 提交对比", "",
      "同一组 32 张 Ascend 950PR，FP8 E4M3，H3584，K8，E384。每项热身 5 轮，再采样 20 轮。",
      "使用相同路由文件生成方式和 seed。每轮取 32 rank 的最慢 native task，再报告 20 轮的最小值、中位数和最大值。",
      "比较列为 e4a9e4c2 相对 fa0eaf8a 的中位数变化。正值表示 e4a9e4c2 更慢。", "",
      "| BS | maxTokens | Profiling | fa0eaf8a 最小/中位/最大 µs | e4a9e4c2 最小/中位/最大 µs | 中位差 |",
      "|---:|---:|---|---:|---:|---:|"]
for row in rows:
    spans = []
    for tag in ("fa0eaf8a", "e4a9e4c2"):
        spans.append(" / ".join(f"{row[tag+'_'+k+'_us']:.3f}" for k in ("min", "median", "max"))
                     if tag+"_median_us" in row else "失败")
    delta = f"{row['e4_vs_fa_median_pct']:+.2f}%" if "e4_vs_fa_median_pct" in row else "—"
    md.append(f"| {row['bs']} | {row['max_tokens']} | {row['mode']} | {spans[0]} | {spans[1]} | {delta} |")
md.extend(["", "逐项后端、ABI、RDMA 数据量和带宽见 dispatch-comparison.csv。",
           "profiling on 的逐角色阶段时间见 stage-comparison.csv。",
           "两套运行库使用各自的精确提交构建。max1 使用允许单 token 并记录生效值的独立测试夹具。",
           "e4a9e4c2 的 max2 沿用此前通过的结果。其他组合本轮实测。"])
(output / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

for name, values in (("dispatch-comparison.csv", rows), ("stage-comparison.csv", phases)):
    if values:
        keys = list(dict.fromkeys(key for value in values for key in value))
        with (output / name).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=keys)
            writer.writeheader()
            writer.writerows(values)
        files.append((output / name, name))
files.append((output / "report.md", "report.md"))
for root in roots:
    files.append((root / "report.md", root.name + "/report.md"))
    files.append((root / "report-status.json", root.name + "/report-status.json"))
bundle_path = output / "TileXR-UBX-Dispatch-commit-comparison.zip"
temporary = bundle_path.with_suffix(".part")
with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=5) as bundle:
    for file, name in files:
        bundle.write(file, name)
with ZipFile(temporary) as bundle:
    if bundle.testzip():
        raise ValueError("corrupt comparison ZIP")
temporary.replace(bundle_path)
sha256 = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
print(json.dumps({"ready": True, "bundle": str(bundle_path), "sha256": sha256,
                  "pairs": rows, "states": states, "trace_count": sum(
                      name.endswith(".perfetto.json") for _, name in files)}, separators=(",", ":")))
'''


def compare(args):
    names = (args["baseline"], args["previous"])
    if any(not re.fullmatch(r"[-a-zA-Z0-9_]+", name) for name in names):
        raise ValueError("invalid test directory")
    with connect(args["host"]) as client, client.open_sftp() as sftp:
        sftp.get_channel().settimeout(40)
        root = PurePosixPath(sftp.normalize(".")) / ".local/state/tilexr-ubx"
        command = "/usr/bin/python3 - " + shlex.join([str(root), *names])
        rc, out, _ = run_remote(client, command, timeout=95, input_text=COMPARE)
        if rc:
            raise RuntimeError("comparison evidence did not meet verification")
        result = json.loads(out)
        if result["ready"]:
            home = Path(os.environ["USERPROFILE"])
            downloads = home / "Downloads"
            target = (downloads if downloads.is_dir() else home) / Path(result["bundle"]).name
            temporary = target.with_suffix(".part")
            sftp.get(result["bundle"], str(temporary))
            with temporary.open("rb") as handle:
                if file_digest(handle, "sha256").hexdigest() != result["sha256"]:
                    raise RuntimeError("comparison ZIP checksum differs")
            temporary.replace(target)
            result["green_windows_bundle"] = str(target)
        return result


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    end = time.monotonic() + args.get("wait_seconds", 0)
    result = {}
    while True:
        try:
            result = compare(args)
        except (OSError, EOFError, paramiko.SSHException):
            if time.monotonic() >= end:
                raise
        if result.get("ready") or time.monotonic() >= end:
            break
        time.sleep(26)
    print(json.dumps(result or {"ready": False, "phase": "status_unavailable"},
                     ensure_ascii=False, separators=(",", ":")))
