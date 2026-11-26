"""Recover the failed launch stage and diagnose remote preflight safely."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re

from benchmark import HOSTS, ssh

MATCH_ERROR = re.compile(
    r"^(?:RuntimeError|TimeoutError|FileNotFoundError|AssertionError|OSError|PermissionError|ValueError|\w+\.TimeoutExpired):"
)


def local_failure():
    folder = Path.cwd() / "runs/replicate4-bs32-50-resume-benchmark/1/execution"
    state = json.loads((folder / "execution.json").read_text())
    assert state["status"] == "EXITED" and state["exit_code"] == 1
    rows = (folder / "stderr.log").read_text(encoding="utf-8", errors="replace").splitlines()
    return [line.strip()[:200] for line in rows if MATCH_ERROR.match(line.strip())][-2:]


def evidence(host, payload):
    return {"host": host, **json.loads(ssh(host, "python3 -", payload=payload, timeout=225))}


here = Path(__file__).resolve().parent
remote = (here / "run_remote.py").read_text().split('if __name__ == "__main__":', 1)[0]
probe = r"""
record = dict(run_directory_exists=OUTPUT.exists(),
              controller_exists=(ROOT/"full-bs32-f50-controller.log").exists(),
              calibration_models=len(list((OUTPUT/"clock-calibration").glob("*.model"))),
              worker_count=len(list(OUTPUT.glob("rank*/worker.log"))),
              rank_records=len(list(OUTPUT.glob("rank*/rounds.jsonl"))),
              traces=len(list(OUTPUT.glob("rank*/dispatch.last.trace.json"))),
              host_result_exists=(OUTPUT/"host-result.json").exists(),
              archive_exists=(OUTPUT/"archive-members.json").exists())
if not record["controller_exists"] and not record["run_directory_exists"]:
    import contextlib
    import io
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            preflight()
        record["preflight"] = "ready"
    except Exception as exc:
        record["preflight"] = f"{type(exc).__name__}: {exc}"[:300]
else:
    errors = []
    logs = [ROOT/"full-bs32-f50-controller.log"]
    logs.extend(sorted((OUTPUT/"clock-calibration").glob("*.log")))
    logs.extend(sorted(OUTPUT.glob("rank*/worker.log")))
    for log in logs:
        if log.is_file():
            for line in log.read_text(errors="replace").splitlines():
                if re.search(r'failed|timeout|exception|error|5070|traceback|assertion', line, re.I):
                    errors.append((str(log.relative_to(ROOT)), line.strip()[:180]))
    record["error_examples"] = errors[-4:]
print(json.dumps(record))
"""
errors = local_failure()
with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(lambda host: evidence(host, (remote + "\n" + probe).encode()), HOSTS))
print(json.dumps({"read_only": True, "launcher_errors": errors, "hosts": results}, separators=(",", ":")))
