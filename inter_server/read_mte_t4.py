"""Read the T4 comparison receipt and summarize retained traces on failure."""

import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root.parent / "alltoall"))
from ssh_transport import run

source = (root / "owner_trace_remote.py").read_text(encoding="utf-8")
source = source.removesuffix('\n' if source.endswith('\n') else '').rsplit('if __name__ == ', 1)[0]
source += """
CASE = CASE.with_name("t4-sst-mte-state-8720cf34")
result = {"case": CASE.name, "exists": CASE.is_dir()}
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
    import time
    log_info = log.stat()
    result["sim_log"] = {
        "bytes": log_info.st_size,
        "seconds_since_update": round(time.time() - log_info.st_mtime),
    }
if status in {"timed_out", "failed", "sst_and_checker_passed"}:
    evidence = inspect()
    result.update({key: evidence[key] for key in (
        "counts", "sq_doorbell_counts", "drain_order_counts",
        "sq_cq_ring_coverage", "post_timeout_dump",
    ) if key in evidence})
print(json.dumps(result, ensure_ascii=True))
"""
print(json.dumps(run(source, timeout=110), ensure_ascii=True))
