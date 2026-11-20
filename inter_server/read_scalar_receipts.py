"""Read both retained scalar-forward receipts from the simulation host."""
import json
import subprocess

source = """
import json
from pathlib import Path

root = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native")
result = {}
for size in ("t4", "t8"):
    case = root / (size + "-sst-mte-scalar-forward-5a6c7c8c")
    item = {"case_exists": case.is_dir()}
    for name in ("run.json", "task-result.json"):
        path = case / name
        if path.is_file():
            try:
                row = json.loads(path.read_text())
            except ValueError:
                continue
            item[name] = {key: row[key] for key in (
                "status", "returncode", "error", "guards", "kernel_sha256",
                "run", "runner_exit_code", "exported_rank_count",
                "checker_log_present", "container_stopped",
            ) if key in row}
    log = case / "sim.log"
    if log.is_file():
        item["simulation_log_bytes"] = log.stat().st_size
    result[size] = item
print(json.dumps(result, ensure_ascii=True))
"""
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode("ascii"), capture_output=True, timeout=35)
proc.check_returncode()
print(json.dumps(json.loads(proc.stdout), ensure_ascii=True))
