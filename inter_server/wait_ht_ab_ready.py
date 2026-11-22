"""Wait for concrete readiness receipts and read each retained HT comparison."""
import json
from pathlib import Path
import subprocess
import sys
import time

root = Path(__file__).resolve().parent
wait_until = time.monotonic() + 1500
while time.monotonic() < wait_until:
    proc = subprocess.run([sys.executable, str(root / "read_ht_ab_status.py")],
                          capture_output=True, timeout=360)
    if proc.returncode == 0:
        snapshot = json.loads(proc.stdout)
        ready = snapshot.get("ready", {})
        integration = snapshot.get("integration", {})
        observed = (*ready.values(), *integration.values())
        if len(observed) == 7 and all(row.get("task-result.json", {}).get("container_stopped")
                                      for row in observed):
            print(json.dumps(snapshot, separators=(",", ":")))
            break
    time.sleep(30)
else:
    if proc.returncode:
        print(json.dumps({"wait_status": "capture_unavailable", "exit_code": proc.returncode}))
    else:
        print(proc.stdout.decode("utf-8").strip())
