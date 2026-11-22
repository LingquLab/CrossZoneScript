"""Add the fixture context to the report and verify the completed server state."""

import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys

from pack_reports import fetch
from stage_sources import connect, run_remote


FINISH = r'''
import json
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
matrix = json.loads((root / "status.json").read_text())
retry = json.loads((root / "retry-status.json").read_text())
counts = json.loads((root / "report-status.json").read_text())
assert matrix["phase"] == "completed" and retry["phase"] == "case_passed"
assert counts["reports"] + len(counts["failed_cases"]) == counts["expected"]
prefix = "tilexr-" + root.name + "-"
for name in ("queue", "read-retry", "report", "recovery.timer"):
    proc = subprocess.run(["systemctl", "is-active", prefix + name],
                          capture_output=True, text=True, timeout=7, check=False)
    assert proc.stdout.strip() == "inactive", name
result = root / "report.md"
old = result.read_text(encoding="utf-8")
header = "## 测试配置与失败范围"
context = """## 测试配置与失败范围

同一组 32 张 Ascend 950PR 上，FP8 Dispatch 的 BS64、BS128，
BF16 sleep-tests Combine 写的 BS32、BS64、BS128，以及 stages-1-7 Combine 读的
BS32、BS64、BS128，均通过 profiling off 和 on 的 32 rank 正确性检查。
5 项失败均出在 stages-1-7 的 Combine 写。BS32 的 profiling on 通过，
BS64 和 BS128 两种模式均失败。读 BS32 profiling off 首次遇到 SSH 中断，
归档后补跑通过。

stages-1-7 读写用例在执行时关闭了 sleep_at。其原夹具每四轮有一轮空路由，
20 个测量轮中包含第 6、10、14、18、22 轮。报告保留实际 RDMA 数据量，
最小值可能来自空路由轮。sleep-tests 使用夹具自带的 sleep_at 与输入生成方式。
Combine profiling on 在各自分支的派生提交中加入阶段采集。"""
if header not in old:
    temp = result.with_suffix(".part")
    temp.write_text(old.rstrip() + "\n\n" + context + "\n", encoding="utf-8")
    temp.replace(result)
print(json.dumps({"reports": counts["reports"], "failed_cases": len(counts["failed_cases"]),
                  "retry": retry["phase"], "services": "inactive"}))
'''


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace = args["namespace"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
        raise ValueError("invalid namespace")
    with connect(args["host"]) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / namespace
        command = "/usr/bin/python3 - " + shlex.quote(str(root))
        code, out, _ = run_remote(client, command, timeout=45, input_text=FINISH)
        if code:
            raise RuntimeError("final state is not yet stable")
        print(out, flush=True)
    package = fetch(args)
    if not package.get("ready"):
        raise RuntimeError("verified report bundle unavailable")
    print(json.dumps({key: package[key] for key in
                      ("green_windows_bundle", "bundle", "sha256", "trace_count")}))
