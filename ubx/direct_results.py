"""Collect the UBX matrix outcome and the first error classes from each test node."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex
import sys

from stage_sources import connect, run_remote


COLLECT = r'''
from collections import Counter
import json
from pathlib import Path
import re
import sys

base = Path(sys.argv[1])
is_coordinator = sys.argv[2] == "1"
runs = base / "runs"
status_path = base / "status.json"
report_status = base / "report-status.json"
result = {}
if is_coordinator:
    if status_path.is_file():
        result["status"] = json.loads(status_path.read_text())
    if report_status.is_file():
        state = json.loads(report_status.read_text())
        result["reported"] = state.get("reports")
        result["report_errors"] = state.get("pending_report_errors", {})
cases = {}
for folder in sorted(runs.iterdir()):
    if not folder.is_dir():
        continue
    result_json = folder / "report.json"
    failure = folder / "failure.json"
    node_done = folder / ("node-0.json" if is_coordinator else "node-1.json")
    if is_coordinator and result_json.is_file():
        run = json.loads(result_json.read_text())
        cases[folder.name] = {"correctness": "pass", "abi": run.get("abi")}
        timing = folder / "timing.json"
        if timing.is_file():
            report = json.loads(timing.read_text())
            cases[folder.name]["native_us"] = report.get("native_task_us", {}).get("round_slowest_rank")
            cases[folder.name]["traces"] = sorted(path.name for path in folder.glob("*.perfetto.json"))
    elif not is_coordinator and node_done.is_file():
        continue
    else:
        issues = Counter()
        for rank in sorted(folder.glob("rank-*")):
            boundary = None
            for file in (rank / "stderr.log", rank / "stdout.log"):
                if file.is_file():
                    sample = file.read_bytes()[-512000:].decode(errors="replace")
                    matches = re.findall(r"(?:UBX_FAILURE|COMBINE_FAILURE).*?error=([^;\n]+)", sample)
                    if not matches:
                        matches = re.findall(r"(?:COMBINE_FAIL|UBX_FAIL)[^\n]*", sample)
                    if matches:
                        boundary = matches[0][:155]
                        break
            if boundary:
                issues[boundary] += 1
        if issues or failure.is_file():
            cases[folder.name] = {"rank_errors": dict(issues), "failed": failure.is_file()}
            if failure.is_file():
                cases[folder.name]["reason"] = json.loads(failure.read_text()).get("reason")
result["cases"] = cases
print(json.dumps(result, separators=(",", ":")))
'''


def query(host, namespace, is_coordinator):
    with connect(host) as client, client.open_sftp() as transfer:
        root = transfer.normalize(".") + "/.local/state/tilexr-ubx/" + namespace
        invocation = "/usr/bin/python3 - " + shlex.quote(root) + (" 1" if is_coordinator else " 0")
        code, output, _ = run_remote(client, invocation, timeout=40, input_text=COLLECT)
        if code:
            raise RuntimeError(f"could not read {host} results")
        return json.loads(output)


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace = args["namespace"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
        raise ValueError("invalid namespace")
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(query, host, namespace, index == 0)
                 for index, host in enumerate(args["hosts"])]
        for host, task in zip(args["hosts"], tasks):
            print(json.dumps({"host": host, **task.result()}, separators=(",", ":")))
