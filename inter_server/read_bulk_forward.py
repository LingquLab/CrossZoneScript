"""Read the aligned-forward run receipts without starting a new simulation."""

import json
from pathlib import Path
import subprocess

source = r"""
import json
from pathlib import Path
import re

mount = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native")
result = {}
for tokens in (4, 8):
    case = mount / f"t{tokens}-sst-mte-bulk-forward-979923e6"
    row = {"case_exists": case.is_dir()}
    for name in ("run.json", "task-result.json"):
        path = case / name
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text())
        except ValueError:
            continue
        fields = ("status", "kernel_sha256", "returncode", "guards", "runner_exit_code",
                  "run", "runner_wall_seconds", "sst_simulated_microseconds",
                  "exported_rank_count", "checker_log_present", "container_stopped",
                  "runner_exception_type")
        row[name] = {key: record[key] for key in fields if key in record}
    sim_log = case / "sim.log"
    if sim_log.is_file():
        with sim_log.open("rb") as stream:
            stream.seek(max(0, sim_log.stat().st_size - 65536))
            log = stream.read().decode("utf-8", "replace")
        cycles = re.findall(r"Sim: ([0-9]+)", log)
        row["log_bytes"] = sim_log.stat().st_size
        if cycles:
            row["last_log_cycle"] = int(cycles[-1])
    result[f"t{tokens}"] = row
print(json.dumps(result, ensure_ascii=True))
"""
if "profile" in Path(__file__).stem:
    source = source.replace('for tokens in (4, 8):', 'for tokens in (4,):')
    source = source.replace('f"t{tokens}-sst-mte-bulk-forward-979923e6"', '"t4-sst-profile-94e104f0"')
    source = source.replace('"runner_exception_type")','"runner_exception_type", "profile")')
    source = source.replace('    result[f"t{tokens}"] = row',
        '    row["profile_raw_dump_count"] = len(list((case / "bin").glob("*_profile.bin")))\n'
        '    row["perfetto_exists"] = (case / "profile-report/perfetto_trace.json").is_file()\n'
        '    result[f"t{tokens}"] = row')
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode("ascii"), capture_output=True, timeout=35)
proc.check_returncode()
print(json.dumps(json.loads(proc.stdout), ensure_ascii=True))
