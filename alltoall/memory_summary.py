"""Return a bounded receipt from a task-owned SST bind."""

import hashlib
import json
from pathlib import Path
import sys


bind, revision, status, side, *cases = sys.argv[1:]
root = Path(bind)


def read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


qual = read(root / "receipts/qualification.json")
summary = dict(acceptance_exit=int(status), revision=qual.get("revision"),
               qualification_sha256=digest(root / "receipts/qualification.json"),
               build_log=str(root / "receipts/build.log"),
               build_log_sha256=digest(root / "receipts/build.log"),
               container_removed="CONTAINER_REMOVED " in (
                   (root / "container-cleanup.log").read_text(encoding="utf-8")
                   if (root / "container-cleanup.log").is_file() else ""),
               cases={})
for case in cases:
    path = root / "results" / case
    controller = read(path / "result.json")
    run = read(path / "run/result.json")
    ranks = read(path / "run/ranks.json")
    if side.startswith("diagnostic-"):
        process = read(path / "run/process.json")
        expected_words = [[2] * 8] + [[1] * 8 for _ in range(7)]
        summary["expected_failure_verified"] = (
            int(status) == 1 and summary["container_removed"] and summary["revision"] == revision
            and controller.get("status") == "failed_or_unknown" and run.get("state") == "failed"
            and process.get("runner_returncode") == 0 and process.get("remaining_processes") == []
            and [row["rank"] for row in ranks] == list(range(8))
            and all(all(row[field] == "passed" for field in ("payload", "guards", "padding"))
                    and row["kernel_status"] == "failed" and row["kernel_status_words"] == expected_words
                    for row in ranks)
            and digest(root / "suites" / case / "kernel.o") == qual["kernels"][case]["sha256"]
        )
    failed = [{key: row[key] for key in ("rank", "payload", "guards", "padding", "kernel_status")}
              for row in ranks if row.get("status") != "passed"]
    row = dict(controller_status=controller.get("status"),
               phases={k: v.get("status") for k, v in controller.get("phases", {}).items()},
               device_state=run.get("state"), entry=run.get("kernel", {}).get("entry"),
               deployed_sha256=run.get("kernel", {}).get("sha256"),
               object_sha256=qual.get("kernels", {}).get(case, {}).get("sha256"),
               runner_returncode=run.get("runner_returncode"),
               rank_count=run.get("rank_count"), ranks_sha256=digest(path / "run/ranks.json"),
               failed_ranks=failed[:4], simulated_us=run.get("simulated_us"), logs=str(path),
               library_mappings=run.get("loaded_libraries", {}).get("state"))
    if case == "memory_direct_schedule_local_cross" and run.get("schedule"):
        schedule = run["schedule"]
        row["schedule"] = {key: schedule.get(key) for key in
                           ("boundary_observed_valid", "spread", "payload_valid", "scheduling_on_time")}
    summary["cases"][case] = row
if int(status) == 0:
    assert summary["container_removed"] and summary["revision"] == revision
    assert all(row["controller_status"] == "passed" and row["runner_returncode"] == 0
               and row["deployed_sha256"] and row["deployed_sha256"] == row["object_sha256"]
               and row["rank_count"] == ranks and not row["failed_ranks"]
               for row, ranks in zip(summary["cases"].values(), (8, 32, 64)))
print(json.dumps(summary))
