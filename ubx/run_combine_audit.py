"""Run only Combine Write with the unchanged production OFF kernel."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
ORIGINAL = ROOT.with_name(sys.argv[2])
sys.path.insert(0, str(ORIGINAL))
import campaign

CASE = ("stages", "write", 32, "off")


def run_node(index, rendezvous, capture):
    campaign.BASE = ROOT
    campaign.package = lambda branch, variant: ROOT / "install"
    campaign.ENV.update(TILEXR_UBX_ROUNDS="4", TILEXR_UBX_COMBINE_FAILURE_AUDIT="1")
    directory = ROOT / "diagnostic"
    directory.mkdir(exist_ok=False)
    route = ORIGINAL / "attempts/initial-ubx/stages-write-bs32-off/routes.bin"
    ranks = range(16 * index, 16 * (index + 1))
    children = [campaign.start_rank(rank, directory, CASE, rendezvous, capture, route) for rank in ranks]
    for job in children:
        job.wait(timeout=120)
    rows = []
    runtime = {}
    for rank in ranks:
        path = directory / f"rank-{rank:02}" / "stderr.log"
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith("UBX_COMBINE_AUDIT "):
                rows.append({key: int(value) for key, value in re.findall(r"(\w+)=(-?\d+)", line)})
            elif line.startswith("UBX_COMBINE_RUNTIME "):
                runtime[rank] = line.split(" detail=", 1)[0]
    groups = Counter((row["status"], row["produced"], row["output"], row["cq"]) for row in rows)
    phases = [{"status": status, "produced": produced, "output": output, "cq": cq, "cores": count}
              for (status, produced, output, cq), count in sorted(groups.items())]
    summary = {"audit_rows": len(rows), "phases": phases, "acl_errors": runtime,
               "pending": [row for row in rows if row["status"] != 0][:14]}
    result = directory / "audit.json"
    result.write_text(json.dumps(summary), encoding="utf-8")


def main(coordinator, peer):
    ready = ORIGINAL / "ready/stages-off"
    queue = "tilexr-" + ORIGINAL.name + "-queue"
    with ThreadPoolExecutor(max_workers=2) as pool:
        other = pool.submit(campaign.bounded_output, campaign.remote_python(peer, "probe", str(ready)))
        own = campaign.occupancy()
        remote = other.result().split()
    if own != "I" * 16 or remote != ["1", "I" * 16]:
        print(json.dumps({"phase": "devices_busy", "coordinator": own, "peer": remote}))
        return
    if subprocess.run(["systemctl", "is-active", "--quiet", queue], check=False).returncode == 0:
        print(json.dumps({"phase": "separate_queue_active"}))
        return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((coordinator, 0))
        rendezvous = f"{coordinator}:{listener.getsockname()[1]}"
    unit = "tilexr-" + ROOT.name + "-single"
    capture = uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = []
        for index, host in enumerate((None, peer)):
            cmd = ["systemd-run", "--quiet", "--wait", "--collect", "--property=Type=exec",
                   "--property=RuntimeMaxSec=210", "--unit=" + unit, sys.executable, str(Path(__file__)),
                   "node", ORIGINAL.name, str(index), rendezvous, capture]
            futures.append(pool.submit(subprocess.run, campaign.process(host, cmd),
                                       capture_output=True, timeout=230, check=False))
        codes = [future.result().returncode for future in futures]
    report = []
    path = ROOT / "diagnostic/audit.json"
    for host in (None, peer):
        value = campaign.bounded_output(campaign.process(host, ["cat", str(path)]))
        report.append(json.loads(value) if value else {})
    print(json.dumps({"phase": "observed", "node_codes": codes, "nodes": report}, separators=(",", ":")))


if __name__ == "__main__":
    if sys.argv[1] == "node":
        run_node(int(sys.argv[3]), sys.argv[4], sys.argv[5])
    else:
        main(sys.argv[3], sys.argv[4])
