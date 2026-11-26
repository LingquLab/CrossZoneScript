"""Check the shared-MR retest's build, worker logs and validation receipts."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from benchmark import HOSTS, ssh

PROBE = r"""
from pathlib import Path
import json
import re

base = Path("/root/ubx-replicate4-e172cd2-bs32-f50")
runtime = base / "runtime-shared-mr"
output = base / "full-bs32-f50-shared-mr"

def tail(path, pattern, limit=4):
    if not path.is_file():
        return []
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - 131072))
        lines = handle.read().decode(errors="replace").splitlines()
    return [line.strip()[:210] for line in lines if re.search(pattern, line, re.I)][-limit:]

status = runtime / "build-status.txt"
receipt = runtime / "runtime-result.json"
controller = base / "full-bs32-f50-shared-mr-controller.log"
worker_logs = sorted(output.glob("rank*/worker.log"))
errors = {}
for path in worker_logs:
    selected = tail(path, r"halMemRegUbSegment|route MR registration:|MR registration failed|"
                          r"clock warmup|complete batch|received route|mismatch|failed")
    if selected:
        errors[path.parent.name] = selected[-2:]
rounds = [sum(1 for unused in p.open()) for p in output.glob("rank*/rounds.jsonl")]
print(json.dumps(dict(
    build_status=status.read_text().strip() if status.is_file() else None,
    build_receipt=json.loads(receipt.read_text()) if receipt.is_file() else None,
    build_errors=tail(runtime/"build.log", r"fatal|CMake Error|error:") if not receipt.is_file() else [],
    calibration_models=len(list((output/"clock-calibration").glob("*.model"))),
    workers=len(worker_logs), worker_error_examples=dict(list(errors.items())[:5]),
    controller_errors=tail(controller, r"Traceback|AssertionError|RuntimeError|TimeoutError"),
    ranks_with_rounds=len(rounds), min_rounds=min(rounds, default=0), max_rounds=max(rounds, default=0),
    traces=len(list(output.glob("rank*/dispatch.last.trace.json"))),
    archived=(output/"archive-members.json").is_file(),
)))
"""


def status(host):
    return dict(host=host, **json.loads(ssh(host, "python3 -", payload=PROBE.encode(), timeout=30)))


def main():
    folder = Path.cwd() / "runs/replicate4-shared-mr-bs32-retest/1/execution"
    saved = folder / "execution.json"
    controller = {}
    if saved.is_file():
        state = json.loads(saved.read_text())
        controller = {key: state.get(key) for key in ("status", "exit_code")}
        if state["status"] == "EXITED" and state["exit_code"] == 0:
            report = json.loads((folder / "stdout.log").read_text(encoding="utf-8"))
            assert report["passed"] and report["core_total_samples"] == 89600
            print(json.dumps(dict(read_only=True, benchmark=report), separators=(",", ":")))
            return
        if state["status"] == "EXITED" and state["exit_code"]:
            rows = (folder / "stderr.log").read_text(encoding="utf-8", errors="replace").splitlines()
            controller["error"] = next((line[:220] for line in reversed(rows)
                                        if "Error:" in line), None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = dict(read_only=True, controller=controller, hosts=list(pool.map(status, HOSTS)))
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
