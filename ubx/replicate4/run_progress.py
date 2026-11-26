"""Read the most recent real workload and device health state."""
from concurrent.futures import ThreadPoolExecutor
import json

from benchmark import HOSTS, ssh

PROBE = r"""
from pathlib import Path
import json
import re
import subprocess

root = Path("/root/ubx-replicate4-e172cd2-bs32-f50")
output = root / "full-bs32-f50"
controller = root / "full-bs32-f50-controller.log"
data = dict(
    run_directory_exists=output.is_dir(), controller_started=controller.is_file(),
    worker_count=len(list(output.glob("rank*/worker.log"))),
    trace_count=len(list(output.glob("rank*/dispatch.last.trace.json"))),
    calibration_models=len(list((output/"clock-calibration").glob("*.model"))),
    verified=(output/"host-result.json").is_file(),
)
if not output.is_dir():
    alerts = []
    busy_devices = []
    for device in range(16):
        check = subprocess.run(["npu-smi", "info", "-t", "health", "-i", str(device)],
                               capture_output=True, text=True, timeout=6)
        health = re.search(r"Health Status\s*:\s*(\S+)", check.stdout)
        value = health.group(1) if health else "UNKNOWN"
        if check.returncode != 0 or value != "OK":
            alerts.append([device, value, check.returncode])
        occupancy = subprocess.run(["npu-smi", "info", "-t", "proc-mem", "-i", str(device)],
                                   capture_output=True, text=True, timeout=6)
        if occupancy.returncode != 0 or "No process in device" not in occupancy.stdout:
            busy_devices.append(device)
    data.update(device_health_alerts=alerts, devices_busy_or_unknown=busy_devices)
print(json.dumps(data, separators=(",", ":")))
"""


def state(host):
    data = ssh(host, "python3 -", payload=PROBE.encode(), timeout=210)
    return {"host": host, **json.loads(data)}


with ThreadPoolExecutor(max_workers=2) as executor:
    print(json.dumps({"read_only": True, "hosts": list(executor.map(state, HOSTS))},
                     separators=(",", ":")))
