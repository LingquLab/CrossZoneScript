"""Read the T4 comparison receipt and summarize retained traces on failure."""

import json
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parent
source = (root / "owner_trace_remote.py").read_text(encoding="utf-8")
source = source.rsplit('if __name__ == ', 1)[0]
source += """
import time
CASE = CASE.with_name("t4-sst-mte-state-8720cf34")
result = {"case": CASE.name, "exists": CASE.is_dir()}
deadline = time.monotonic() + WAIT_SECONDS
while (CASE / "run.json").is_file() and not (CASE / "task-result.json").is_file():
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        break
    time.sleep(min(15, remaining))
for name in ("run.json", "task-result.json"):
    try:
        row = json.loads((CASE / name).read_text())
        result[name] = {key: row[key] for key in (
            "status", "returncode", "error", "runner_exit_code", "run",
            "exported_rank_count", "checker_log_present", "container_stopped",
        ) if key in row}
    except FileNotFoundError:
        continue
    except ValueError:
        result[name] = {"status": "updating"}
status = result.get("run.json", {}).get("status")
log = CASE / "sim.log"
if log.is_file():
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 65536))
        tail = stream.read().decode("utf-8", "replace")
    logged_cycles = [int(value) for value in re.findall(r"Sim: ([0-9]+)", tail)]
    log_info = log.stat()
    result["sim_log"] = {
        "bytes": log_info.st_size,
        "last_sample_max_cycle": max(logged_cycles, default=None),
    }
if status in {"running", "timed_out", "failed", "sst_and_checker_passed"}:
    evidence = inspect()
    result.update({key: evidence[key] for key in (
        "counts", "sq_doorbell_counts", "drain_order_counts",
        "sq_cq_ring_coverage", "post_timeout_dump",
    ) if key in evidence})
print(json.dumps(result, ensure_ascii=True))
"""
wait_seconds = 1500 if Path(__file__).stem.startswith("wait_") else 0
source = "WAIT_SECONDS = " + repr(wait_seconds) + "\n" + source
compile(source, "<remote_status>", "exec")
try:
    proc = subprocess.run(
        ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
         "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"],
        input=source.encode("utf-8"), capture_output=True, timeout=wait_seconds + 120,
    )
    if proc.returncode == 0:
        payload = json.loads(proc.stdout)
    else:
        diagnostic = proc.stderr.decode("utf-8", "replace")
        classes = re.findall(r"^([A-Za-z]+(?:Error|Exception)):", diagnostic, re.MULTILINE)
        lines = re.findall(r'File "<stdin>", line (\d+)', diagnostic)
        payload = {"observation": "status_command_failed", "ssh_exit_code": proc.returncode,
                   "error_classes": classes[-3:], "remote_source_lines": lines[-4:]}
except (OSError, ValueError, subprocess.TimeoutExpired) as error:
    payload = {"observation": "transport_failed", "error_type": type(error).__name__}
print(json.dumps(payload, ensure_ascii=True))
