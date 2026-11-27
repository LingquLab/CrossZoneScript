"""Retain bounded launch, Flag, QP and runtime evidence after a warmup failure."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex

from benchmark import HOSTS, ssh
from retest_local_receive import CONTROL_WORK, REVISION, WORK

PROBE = r"""
import json
from pathlib import Path
import re
import sys

base = Path(sys.argv[1])
output = base / sys.argv[2]
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
controller = base / (output.name + "-controller.log")
tail = controller.read_text(errors="replace").splitlines()[-6:] if controller.is_file() else []
build = base / "build-result.json"
cleanup = base / (output.name + "-npu-cleanup.json")
print(json.dumps(dict(workers=entries, runtime=runtime[:8], controller_log=tail,
    cleanup=json.loads(cleanup.read_text()) if cleanup.is_file() else None,
    traces=len(list(output.glob("rank*/dispatch.last.trace.json"))),
    controller_started=controller.is_file(), host_result=(output/"host-result.json").is_file(),
    build_revision=json.loads(build.read_text())["revision"] if build.is_file() else None), separators=(",",":")))
"""


def inspect(host, work, run_name):
    result = ssh(host, shlex.join(["python3", "-", work, run_name]), payload=PROBE.encode(), timeout=85)
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
    active = {kind: [0] * (16 if kind == "sender" else 4) for kind in ("sender", "route")}
    recv_nonzero = 0
    for worker in workers:
        flags = worker.get("failure-flags", {})
        for q in flags.get("sender_qps", []):
            index = q["aiv"] - (24 if q["queue"] == "route" else 0)
            if any(q["template_epoch"]):
                active[q["queue"]][index] += 1
        recv_nonzero += sum(count for b in flags.get("buffers", []) if b["name"] == "recv_fragments"
                            for epoch, count in b.get("epochs", []) if epoch)
    return dict(host=host["host"], workers=len(workers), ranks_with_records=sum(w["records"] > 0 for w in workers),
                captured_flags=sum("failure-flags" in w for w in workers),
                controller_started=host["controller_started"], host_result=host["host_result"],
                cleanup=host["cleanup"], traces=host["traces"], build_revision=host["build_revision"],
                template_copies_by_core=active, recv_nonzero_fragments=recv_nonzero,
                examples=examples[:1], runtime=host["runtime"][:1])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    options = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    destination = Path(options["destination"]) / ("replicate4-local-receive-bs32-" + REVISION[:7] + "-diagnostics")
    control = options.get("control", False)
    work = CONTROL_WORK if control else WORK
    run_name = options.get("run_name", "full-bs32-f50")
    assert re.fullmatch(r"full-bs32-f50(?:-[a-z0-9-]+)?", run_name)
    task = options.get("task")
    execution = None
    if task:
        assert re.fullmatch(r"[a-z0-9-]+", task)
        execution_dir = Path.cwd() / "runs" / task / str(int(options.get("iteration", 1))) / "execution"
        receipt_path = execution_dir / "execution.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {}
        execution = {"status": receipt.get("status", "PENDING"), "exit_code": receipt.get("exit_code")}
        if execution["exit_code"] not in (None, 0):
            errors = execution_dir / "stderr.log"
            lines = errors.read_text(encoding="utf-8", errors="replace").splitlines() if errors.is_file() else []
            execution["error"] = next((line[:270] for line in reversed(lines) if "Error:" in line), None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        hosts = list(pool.map(lambda host: inspect(host, work, run_name), HOSTS))
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / ("warmup-evidence.json" if run_name == "full-bs32-f50" else run_name + "-evidence.json")
    output.write_text(json.dumps(dict(execution=execution, hosts=hosts), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(local_diagnostics=str(output), execution=execution, hosts=list(map(compact, hosts))), separators=(",", ":")))
