"""Retry after verifying that the earlier failure had no workload effects."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from benchmark import HOSTS, OUTPUT, ROOT, main, ssh

prior = Path.cwd() / "runs/replicate4-bs32-50-resume-benchmark/1/execution"
state = json.loads((prior / "execution.json").read_text())
assert state["status"] == "EXITED" and state["exit_code"] == 1, state["status"]
command = f"test ! -e {OUTPUT} && test ! -e {ROOT}/full-bs32-f50-controller.log"
with ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(lambda host: ssh(host, command, stage="verify previous run did not start"), HOSTS))
main(ports=(43000, 46000, 48200))
