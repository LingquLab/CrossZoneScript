"""Analyze verified server records without running an NPU workload."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

from benchmark import HOSTS, OUTPUT, read_verifier, ssh, summarize


def read_state(host):
    names = ("host-result.json", "archive-members.json")
    command = " ".join(
        f"if test -f {OUTPUT / name}; then printf '1 '; else printf '0 '; fi;"
        for name in names
    )
    receipt, archive = [v == "1" for v in ssh(host, command).decode().split()]
    return {"host": host, "host_result_exists": receipt, "archive_exists": archive}


with ThreadPoolExecutor(max_workers=2) as pool:
    deadline = time.monotonic() + 330
    outcome = Path.cwd() / "runs/replicate4-bs32-50-procfs-cleanup/1/execution/execution.json"
    while True:
        states = list(pool.map(read_state, HOSTS))
        if all(item["host_result_exists"] and item["archive_exists"] for item in states):
            break
        if outcome.exists():
            receipt = json.loads(outcome.read_text())
            if receipt["status"] not in {"STARTING", "RUNNING"}:
                break
        if time.monotonic() >= deadline:
            break
        time.sleep(10)
    result = {"read_only": True, "hosts": states}
    if all(item["host_result_exists"] and item["archive_exists"] for item in states):
        result["benchmark"] = summarize(pool, read_verifier())
    print(json.dumps(result, separators=(",", ":")))
