"""Read the failed assistant Dispatch boundaries and current node progress."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path, PurePosixPath
import shlex
import sys

from stage_sources import connect, run_remote
from tail_queue import ASSIST_NAMESPACE


INSPECT = r'''
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys

root = Path(sys.argv[1])
index = int(sys.argv[2])
cases = {}
for folder in sorted((root / "runs").glob("tail-reviewed-dispatch-*")):
    local_done = (folder / f"node-{index}.json").is_file()
    timing = folder / "timing.json"
    if timing.is_file():
        report = json.loads(timing.read_text())
        cases[folder.name] = {"native_us": report["native_task_us"]["round_slowest_rank"],
                              "copyout_rows_by_role": report.get("packet_diagnostics", {}).get("max", {}).get("copyout_rows_by_role")}
        continue
    counts, errors = Counter(), Counter()
    for rank in sorted(folder.glob("rank-*")):
        counts[len(list(rank.glob("*.summary.json")))] += 1
        for name in ("stderr.log", "stdout.log"):
            path = rank / name
            if not path.is_file():
                continue
            lines = path.read_text(errors="replace")
            matches = re.findall(r"UBX_FAILURE[^\n]*?error=([^;\n]+)", lines)
            for message in set(matches):
                errors[message.strip()[:145]] += 1
    summary = {"done": local_done, "rank_round_counts": dict(counts), "rank_errors": dict(errors)}
    failure = folder / "failure.json"
    if failure.is_file():
        summary["failure"] = json.loads(failure.read_text()).get("reason")
    if errors or failure.is_file() or (counts and not local_done):
        service = "tilexr-" + root.name + "-" + folder.name
        journal = subprocess.run(["journalctl", "-q", "-u", service, "-n", "18", "--no-pager", "-o", "cat"],
                                 capture_output=True, text=True, timeout=8)
        summary["service_errors"] = [
            line[:160] for line in journal.stdout.splitlines()
            if re.search(r"Error:|exit-code|killed|broken pipe|timed out|connection reset", line, re.I)
        ][-4:]
    cases[folder.name] = summary
result = {"cases": cases}
if index == 0:
    for name in ("status.json", "report-status.json", "retry-status.json", "retry-remaining.json"):
        path = root / name
        if path.is_file():
            result[name.split(".")[0]] = json.loads(path.read_text())
print(json.dumps(result, separators=(",", ":")))
'''


def inspect(host, index):
    with connect(host) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / ASSIST_NAMESPACE
        cmd = "/usr/bin/python3 -B - " + shlex.join([str(root), str(index)])
        code, output, _ = run_remote(client, cmd, timeout=45, input_text=INSPECT)
        if code:
            raise RuntimeError("could not inspect assistant case evidence")
        return {"host": host, **json.loads(output)}


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(inspect, host, index) for index, host in enumerate(args["hosts"])]
        for job in jobs:
            print(json.dumps(job.result(), separators=(",", ":")))
