"""Read the final resumed benchmark receipt, including worker evidence on failure."""
import json
from pathlib import Path
import time

from shared_mr_status import main

receipt = Path.cwd() / "runs/replicate4-shared-mr-bs32-resume/1/execution/execution.json"
deadline = time.monotonic() + 480
while time.monotonic() < deadline:
    if receipt.is_file():
        state = json.loads(receipt.read_text(encoding="utf-8"))
        if state["status"] not in {"STARTING", "RUNNING"}:
            break
    time.sleep(8)
main()
