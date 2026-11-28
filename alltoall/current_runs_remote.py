"""Project controller and device results from session-owned bind directories."""

import hashlib
import json
from pathlib import Path
import sys


def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def memory_receipt(root):
    tests = {}
    for path in sorted((root / "results").glob("memory_*")):
        controller = read(path / "result.json")
        run = read(path / "run/result.json")
        subprocess = read(path / "run/process.json")
        case = path.name.removeprefix("memory_")
        tests[case] = [controller.get("status"), run.get("state"), subprocess.get("runner_returncode")]
    qualification = read(root / "receipts/qualification.json")
    objects = {name: row["sha256"] for name, row in qualification.get("kernels", {}).items()}
    return dict(qualified=bool(qualification), object_set=hashlib.sha256(json.dumps(objects, sort_keys=True).encode()).hexdigest() if objects else None,
                container_removed=(root / "container-cleanup.log").is_file(), cases=tests)


def lr8_receipt(root):
    artifacts = (root / "16p-qualified/cmake-artifact.json").is_file()
    suite = root / "16p-suite"
    execution = read(suite / "execution.json")
    return dict(qualified=artifacts, suite_exists=suite.is_dir(), state=execution.get("state"),
                runner_exit=execution.get("runner_returncode"))


if __name__ == "__main__":
    root = Path("/opt")
    prefix = "tilexr-sst-08f501a0-"
    if sys.argv[1] == "memory":
        binds = sorted(root.glob(f"{prefix}candidate.*"), key=lambda p: p.stat().st_mtime)[-3:]
        result = {p.name.removeprefix(prefix): memory_receipt(p) for p in binds}
    else:
        result = {p.name.removeprefix(prefix): lr8_receipt(p) for p in root.glob(f"{prefix}lr8-sim-split*.*")}
    print(json.dumps(result, separators=(",", ":")))
