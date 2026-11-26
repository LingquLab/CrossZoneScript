"""Wait for the replicate16 benchmark and report verified results or failure evidence."""
import json
from pathlib import Path
import time

from disjoint_routes_status import main
from retest_replicate16 import REVISION, WORK

TASK = "replicate16-disjoint-bs32-retest"
receipt = Path.cwd() / f"runs/{TASK}/1/execution/execution.json"
deadline = time.monotonic() + 480
while time.monotonic() < deadline:
    if receipt.is_file():
        state = json.loads(receipt.read_text(encoding="utf-8"))
        if state["status"] not in {"STARTING", "RUNNING"}:
            break
    time.sleep(8)
main(task=TASK, revision=REVISION, work=WORK, mode="replicate16")
