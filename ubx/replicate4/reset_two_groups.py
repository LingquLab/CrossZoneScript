"""Reset the idle cards on the two-group benchmark hosts and verify recovery."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
from subprocess import TimeoutExpired
import time

import benchmark
import reset_for_ep32
from retest_two_groups import HOSTS, ROOT, main as benchmark_main


HERE = Path(__file__).resolve().parent
JOB = ROOT / "npu-reset-two-groups"
STATUS = reset_for_ep32.REMOTE.split('if mode == "reset":', 1)[0]
STATUS = STATUS.replace("reset-ep32-20261127.jsonl", "history.jsonl")
HELPERS = (HERE / "run_remote.py").read_text().split("def start_time(", 1)[0]

RESET = r"""
deadline = time.monotonic() + 850
for device in devices:
    if time.monotonic() >= deadline:
        break
    before = query_health(device)
    if before["status"].lower() == "ok":
        continue
    if before["status"] == "unknown" or occupancy(device) != 0:
        save(dict(device=device, stopped="busy or unknown"))
        continue
    save(dict(device=device, stage="reset-started"))
    try:
        result = subprocess.run(["npu-smi", "set", "-t", "reset", "-i", str(device), "-m", "1"],
                                input="y\n", capture_output=True, text=True, timeout=110)
    except subprocess.TimeoutExpired:
        save(dict(device=device, stopped="reset timed out"))
        break
    save(dict(device=device, reset_exit=result.returncode,
              output=(result.stdout + result.stderr).strip().splitlines()[-4:]))
    time.sleep(20)
    if query_health(device)["status"] == "unknown" and occupancy(device) is None:
        time.sleep(15)
(work / "done").touch()
"""

LAUNCH = r"""
import pathlib
import subprocess
import sys

job = pathlib.Path(sys.argv[1])
job.mkdir()
entry = job / "reset.py"
entry.write_bytes(sys.stdin.buffer.read())
with (job / "driver.log").open("wb") as log:
    process = subprocess.Popen(["python3", str(entry), "reset", str(job), *map(str, range(16))],
                               stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
(job / "pid").write_text(str(process.pid))
"""

REPORT = r"""
report = status()
final = work / "done"
pid_file = work / "pid"
running = False
if pid_file.is_file() and not final.is_file():
    process = Path("/proc") / pid_file.read_text().strip() / "cmdline"
    try:
        running = os.fsencode(work / "reset.py") in process.read_bytes().split(b"\0")
    except OSError:
        pass
report.update(running=running, done=final.is_file(), launched=pid_file.is_file(),
              driver_failed=(work / "driver.log").is_file() and (work / "driver.log").stat().st_size > 0)
print(json.dumps(report))
"""


def launch(host):
    command = shlex.join(["python3", "-c", LAUNCH, str(JOB)])
    return benchmark.ssh(host, command, payload=(HELPERS + STATUS + RESET).encode(), timeout=35, stage="start NPU reset")


def probe(host):
    command = shlex.join(["python3", "-", "status", str(JOB), *map(str, range(16))])
    try:
        output = benchmark.ssh(host, command, payload=(HELPERS + STATUS + REPORT).encode(),
                               timeout=55, stage="NPU reset status")
    except (RuntimeError, TimeoutExpired) as error:
        return dict(host=host, ssh_error=str(error) if isinstance(error, RuntimeError) else "SSH timeout")
    return dict(host=host, **json.loads(output))


def compact(report):
    if "ssh_error" in report:
        return report
    rows = report["devices"]
    history = report["history"]
    return dict(host=report["host"], running=report["running"], done=report["done"], launched=report["launched"],
                healthy=sum(row["status"].lower() == "ok" for row in rows),
                idle=sum(row["occupancy"] == 0 for row in rows),
                busy=[row["device"] for row in rows if row["occupancy"]],
                unknown=sum(row["status"] == "unknown" or row["occupancy"] is None for row in rows),
                reset_ok=len(report["reset_completed"]), driver_failed=report["driver_failed"],
                unsuccessful=[dict(device=row["device"], exit=row.get("reset_exit"), reason=row.get("stopped"))
                              for row in history if row.get("stopped") or row.get("reset_exit", 0) != 0],
                last=history[-1] if history else None)


def wait_for_recovery(pool, hosts, output):
    deadline = time.monotonic() + 970
    settled_since = None
    while True:
        reports = list(pool.map(probe, hosts))
        states = list(map(compact, reports))
        output.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
        if all(state.get("done") and state.get("healthy") == 16 and state.get("idle") == 16 for state in states):
            return
        now = time.monotonic()
        if all(state.get("done") for state in states):
            if settled_since is None:
                settled_since = now
        else:
            settled_since = None
        if now >= deadline or (settled_since is not None and now - settled_since >= 80):
            raise RuntimeError("NPUs did not recover after reset: " + json.dumps(states, separators=(",", ":")))
        time.sleep(15)


def main(args):
    mode = args["mode"]
    hosts = args.get("hosts", HOSTS)
    assert hosts and set(hosts) <= set(HOSTS)
    destination = Path(args["destination"]) / ("replicate4-rdma-two-groups-bs32-af8c365-diagnostics")
    destination.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        if mode == "benchmark":
            assert tuple(hosts) == HOSTS
            wait_for_recovery(pool, hosts, destination / "npu-reset-evidence-recovery.json")
            return benchmark_main({**args, "mode": "resume"})
        if mode == "reset":
            list(pool.map(launch, hosts))
        else:
            assert mode == "status"
        reports = list(pool.map(probe, hosts))
    output = destination / ("npu-reset-evidence-" + mode + ".json")
    output.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    return [compact(report) for report in reports]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    print(json.dumps(main(args), separators=(",", ":")))
