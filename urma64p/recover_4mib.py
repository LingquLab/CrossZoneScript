"""Run the two skipped 4 MiB schedules after the active matrix releases its container."""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
request = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
prior = Path.cwd() / "runs/urma64p-performance-remaining/1/execution/execution.json"
limit = time.monotonic() + 16000
print(json.dumps({"stage": "waiting_for_16mib_batch"}), flush=True)
while time.monotonic() < limit:
    if prior.is_file():
        state = json.loads(prior.read_text(encoding="utf-8"))
        if state.get("status") in {"EXITED", "TIMED_OUT", "CANCELLED", "TERMINATION_UNCONFIRMED"}:
            break
    time.sleep(30)
else:
    print(json.dumps({"stage": "wait_timeout"}), flush=True)
    raise SystemExit(1)

ssh = [
    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
    "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
    "root@141.61.94.30", "docker ps --format '{{.Names}}'",
]
check = subprocess.run(ssh, capture_output=True, text=True, timeout=35)
released = check.returncode == 0 and "urma64p-cases-qdblagat" not in check.stdout.splitlines()
print(json.dumps({"stage": "16mib_batch_released", "released": released,
                  "batch_status": state["status"], "batch_exit": state.get("exit_code")}), flush=True)
if not released:
    raise SystemExit(1)

runner = Path(__file__).resolve().with_name("run_cases.py")
with tempfile.TemporaryDirectory(prefix="urma64p-4mib-") as temp:
    config = Path(temp) / "case.json"
    config.write_text(json.dumps(dict(
        build=request["build"], kind="performance", size="4mib-per-rank",
        timeout=3600, deadline=request["bootstrap_deadline"], schedules=["nosync", "clock"],
    )), encoding="utf-8")
    raise SystemExit(subprocess.run([sys.executable, str(runner), "--args-file", str(config)]).returncode)
