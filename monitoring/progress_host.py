"""Read only the output of the matching validation revisions."""
import json
from pathlib import Path
import re

root = Path("/opt")


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def most_recent(paths):
    return sorted(paths, key=lambda path: path.stat().st_mtime)[-3:]


def log_errors(path):
    try:
        with path.open("rb") as log:
            log.seek(max(0, path.stat().st_size - 8192))
            lines = log.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    return [line.strip()[:220] for line in lines
            if re.search(r"fatal error:| error:|^Error:|Error: .*|^FAILED", line)][-2:]


result = dict(memory=[], lr8=[], full=[], clos=[])
for path in most_recent(root.glob("tilexr-final-memory-*")):
    build = read(path / "result/build-summary.json")
    batch = read(path / "result/batch-final-smoke.json")
    if build and build.get("runtime_commit") != revisions["memory"]:
        continue
    result["memory"].append(dict(work=path.name, build=build.get("status"), batch=batch.get("status"),
                                 completed=len(batch.get("records", [])), active=batch.get("active"),
                                 errors=log_errors(path / "build.stderr.log")))
for path in most_recent(root.glob("tilexr-lr8-*final-" + revisions["urma"][:8] + "-lr8-*")):
    logs = most_recent(path.glob("*.log"))
    cases = [dict(case=receipt.parent.name, value=report.get("simulated_us"), state=report.get("state"))
             for receipt in path.glob("*i32-*/execution.json") if (report := read(receipt))]
    result["lr8"].append(dict(work=path.name, cases=cases, last=[item.name for item in logs],
                              errors=log_errors(logs[-1]) if logs else []))
for prefix, key in (("urma64p-cases.*", "full"), ("urma16p-*", "clos")):
    for path in most_recent(root.glob(prefix)):
        cases = []
        for scenario in path.glob("**/scenario.json"):
            record = read(scenario)
            if record.get("candidate_revision") != revisions["urma"]:
                continue
            reports = most_recent(scenario.parent.glob("runs/*/run.json"))
            last = read(reports[-1]) if reports else {}
            cases.append(dict(case=scenario.parent.name, state=last.get("status")))
        if cases:
            result[key].append(dict(work=path.name, cases=cases))
print(json.dumps(result))
