"""Wait for the disjoint route benchmark and read terminal evidence."""
import json
from pathlib import Path
import time

from disjoint_routes_status import TASK, main

receipt = Path.cwd() / f"runs/{TASK}/1/execution/execution.json"
deadline = time.monotonic() + 540
while time.monotonic() < deadline:
    if receipt.is_file():
        state = json.loads(receipt.read_text(encoding="utf-8"))
        if state["status"] not in {"STARTING", "RUNNING"}:
            break
    time.sleep(8)
main()
