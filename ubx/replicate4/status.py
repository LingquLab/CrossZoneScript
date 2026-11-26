"""Read bounded build and benchmark diagnostics from the two task directories."""
import concurrent.futures
import json

from benchmark import ssh, HOSTS

PROBE = r"""
import json
from pathlib import Path
import re

root = Path("/root/ubx-replicate4-e172cd2-bs32-f50")
run = root / "full-bs32-f50"
status = root / "build-status.txt"
build = root / "build-result.json"

def last_errors(path):
    if not path.is_file():
        return []
    markers = re.compile(r'error|fail|timeout|denied|resolve|assert|traceback', re.I)
    lines = [line.strip() for line in path.read_text(errors="replace").splitlines()]
    errors = [line[:220] for line in lines if markers.search(line)]
    sanitized = [re.sub(r'https?://[^ /]*@', '[redacted]@', line) for line in errors]
    return sanitized[-3:]

workers = sorted(run.glob("rank*/worker.log"))
calibrators = sorted(run.glob("clock-calibration/*.log"))
print(json.dumps(dict(
    build_directory_exists=root.is_dir(), build_status=status.read_text().strip() if status.exists() else None,
    build_receipt_exists=build.exists(), build_errors=last_errors(root / "build.log"),
    workload_directory_exists=run.is_dir(),
    controller_errors=last_errors(root / "full-bs32-f50-controller.log"),
    calibration_logs=len(calibrators),
    calibration_error_examples=[(p.name, errors) for p in calibrators
                                if (errors := last_errors(p))][:2],
    workers=len(workers), worker_error_examples=[(p.parent.name, errors) for p in workers
                                                 if (errors := last_errors(p))][:2],
    host_result_exists=(run / "host-result.json").exists(),
    trace_count=len(list(run.glob("rank*/dispatch.last.trace.json"))),
)))
"""


def status(host):
    return {"host": host, **json.loads(ssh(host, "python3 -", payload=PROBE.encode()))}


with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    print(json.dumps({"read_only": True, "hosts": list(pool.map(status, HOSTS))}, separators=(",", ":")))
