"""Retry after verifying that the earlier failure had no workload effects."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

from benchmark import HOSTS, OUTPUT, ROOT, main, ssh

prior = Path.cwd() / "runs/replicate4-bs32-50-retry-prelaunch/1/execution/execution.json"
deadline = time.monotonic() + 390
while time.monotonic() < deadline:
    if prior.is_file():
        state = json.loads(prior.read_text())
        if state["status"] == "EXITED":
            assert state["exit_code"] == 1, "Previous launch already passed"
            break
        assert state["status"] in {"STARTING", "RUNNING"}, state["status"]
    time.sleep(5)
else:
    raise TimeoutError("No terminal receipt for the previous launch")

command = f"test ! -e {OUTPUT} && test ! -e {ROOT}/full-bs32-f50-controller.log"
with ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(lambda host: ssh(host, command, stage="verify previous run did not start"), HOSTS))
main(ports=(43000, 46000, 48200))
