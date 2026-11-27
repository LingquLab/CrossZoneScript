"""Retain bounded launch, Flag, QP and runtime evidence after a warmup failure."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex

from benchmark import HOSTS, ssh
from retest_local_receive import REVISION, WORK

PROBE = r"""
import json
from pathlib import Path
import re
import sys

output = Path(sys.argv[1]) / "full-bs32-f50"
workers = sorted(output.glob("rank*/worker.log"))
entries = []
for path in workers:
    rank = path.parent
    item = dict(rank=rank.name, records=sum(1 for _ in (rank/"rounds.jsonl").open()) if (rank/"rounds.jsonl").exists() else 0)
    for name in ("failure-flags", "failure-progress"):
        evidence = rank/(name+".json")
        if evidence.is_file():
            item[name] = json.loads(evidence.read_text())
    failures = [line.strip()[:220] for line in path.read_text(errors="replace").splitlines()
                if re.search(r"507046|warmup:|kernel launch:|\[error\]",line,re.I)]
    item["errors"] = failures[:3]
    entries.append(item)
runtime = []
if workers:
    stop = max(p.stat().st_mtime for p in workers)
    for log in sorted(Path("/root/ascend/log/plog").glob("plog-*.log")):
        if not stop-220 <= log.stat().st_mtime <= stop+45:
            continue
        with log.open("rb") as source:
            source.seek(max(0,log.stat().st_size-65536))
            lines = source.read().decode(errors="replace").splitlines()
        errors = [line.strip()[:280] for line in lines
                  if "[ERROR]" in line and "ubx_dispatch_hardware_ep32" in line]
        if errors:
            runtime.append(dict(file=log.name, errors=errors[:5]))
print(json.dumps(dict(workers=entries, runtime=runtime[:8]), separators=(",",":")))
"""


def inspect(host):
    result = ssh(host, shlex.join(["python3", "-", WORK]), payload=PROBE.encode(), timeout=85)
    return dict(host=host, **json.loads(result))


def compact(host):
    workers = host["workers"]
    examples = []
    for worker in workers[:3]:
        flags = worker.get("failure-flags", {})
        queues = flags.get("sender_qps", [])
        examples.append(dict(rank=worker["rank"], errors=worker["errors"],
                             submitted_rounds=flags.get("submitted_rounds"),
                             completed_epoch=flags.get("completed_epoch"),
                             buffer_epochs={b["name"]: b.get("epochs") for b in flags.get("buffers", [])},
                             pending_by_queue={kind: sum(q["pending_wrs"] != 0 for q in queues if q["queue"] == kind)
                                               for kind in ("sender", "route")},
                             active_template_queues={kind: sum(any(q.get("template_epoch", [])) for q in queues if q["queue"] == kind)
                                                     for kind in ("sender", "route")}))
    return dict(host=host["host"], workers=len(workers), ranks_with_records=sum(w["records"] > 0 for w in workers),
                captured_flags=sum("failure-flags" in w for w in workers),
                examples=examples, runtime=host["runtime"][:1])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    options = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    destination = Path(options["destination"]) / ("replicate4-local-receive-bs32-" + REVISION[:7] + "-diagnostics")
    with ThreadPoolExecutor(max_workers=2) as pool:
        hosts = list(pool.map(inspect, HOSTS))
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "warmup-evidence.json"
    output.write_text(json.dumps(hosts, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(local_diagnostics=str(output), hosts=list(map(compact, hosts))), separators=(",", ":")))
