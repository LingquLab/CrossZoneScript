"""Recover an occupied preflight only after all previous launch effects are known."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import time

from benchmark import HOSTS, OUTPUT, REVISION, ROOT, main, ssh


def recover():
    prior = Path.cwd() / "runs/replicate4-bs32-50-occupied-recovery/1/execution"
    receipt = prior / "execution.json"
    deadline = time.monotonic() + 580
    while time.monotonic() < deadline:
        if receipt.is_file():
            state = json.loads(receipt.read_text())
            if state["status"] == "EXITED":
                break
            assert state["status"] in {"STARTING", "RUNNING"}, state["status"]
        time.sleep(5)
    else:
        raise TimeoutError("Previous launch has no terminal receipt")

    if state["exit_code"] == 0:
        report = json.loads((prior / "stdout.log").read_text(encoding="utf-8"))
        assert report["passed"] and report["revision"] == REVISION
        assert report["core_total_samples"] == 89600 and report["last_trace_round"] == 59
        print(json.dumps(report, separators=(",", ":")))
        return

    errors = (prior / "stderr.log").read_text(encoding="utf-8", errors="replace")
    if not re.search(r"preflight-cleanup exited .*AttributeError: module 'os' has no attribute 'pidfd_open'", errors):
        raise RuntimeError("The previous launch did not exit at the unsupported preflight cleanup call")
    command = f"test ! -e {OUTPUT} && test ! -e {ROOT}/full-bs32-f50-controller.log"
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda host: ssh(host, command, stage="verify no workload was started"), HOSTS))
    main(ports=(43000, 46000, 48200), cleanup=True)


if __name__ == "__main__":
    recover()
