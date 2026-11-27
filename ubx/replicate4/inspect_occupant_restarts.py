"""Identify ownership and restart behavior of the processes holding the requested cards."""
from concurrent.futures import ThreadPoolExecutor
import json
import shlex

from benchmark import HOSTS, ssh

PROBE = r"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

def device(device_id):
    result = subprocess.run(["npu-smi", "info", "-t", "proc-mem", "-i", str(device_id)],
                            capture_output=True, text=True, timeout=10)
    if result.returncode or not re.search(rf"NPU ID\s*:\s*{device_id}\b", result.stdout):
        return device_id, None
    pids = {int(p) for p in re.findall(r"Process id\s*:\s*(\d+)", result.stdout, re.I)}
    return device_id, pids if pids or "No process in device" in result.stdout else None

def process(pid):
    folder = Path("/proc") / str(pid)
    try:
        fields = (folder / "stat").read_text().rsplit(") ", 1)[1].split()
        name = (folder / "comm").read_text().strip()
        groups = (folder / "cgroup").read_text().splitlines()
    except OSError:
        return None
    kind = (
        "scheduler" if re.search(r"slurm|kubepods|kubectl|kubelet|pbs|flux", " ".join(groups), re.I)
        else "container" if re.search(r"docker|containerd|libpod|crio", " ".join(groups), re.I)
        else "systemd-service" if any(".service" in group for group in groups)
        else "user-session"
    )
    # Export process labels and grouping counts while leaving job paths and arguments on the server.
    label = re.sub(r"[^A-Za-z0-9_.-]", "?", name)[:32]
    group_token = hashlib.sha256("\n".join(groups).encode()).hexdigest()[:8]
    return dict(identity=(pid, fields[19]), name=label, parent=int(fields[1]),
                state=fields[0], kind=kind, group=group_token)

def sample():
    with ThreadPoolExecutor(max_workers=8) as pool:
        cards = dict(pool.map(device, range(16)))
    processes = {}
    for pids in cards.values():
        for pid in pids or ():
            if pid in processes:
                continue
            info = process(pid)
            if info:
                ancestors = []
                parent = info["parent"]
                while parent > 1 and len(ancestors) < 5:
                    ancestor = process(parent)
                    if not ancestor:
                        break
                    ancestors.append(ancestor["name"])
                    parent = ancestor["parent"]
                info["ancestors"] = ancestors
                processes[pid] = info
    return cards, processes

results = []
older = set()
for attempt in range(3):
    if attempt:
        time.sleep(2)
    cards, processes = sample()
    ids = {tuple(p["identity"]) for p in processes.values()}
    groups = Counter((p["kind"], p["name"], tuple(p["ancestors"]), p["group"]) for p in processes.values())
    results.append(dict(busy=[i for i, pids in cards.items() if pids],
                        unknown=[i for i, pids in cards.items() if pids is None],
                        count=len(processes),
                        pids_new=len(ids-older) if attempt else None,
                        groups=[dict(kind=k, process=p, ancestors=q, group=g, count=n)
                                for (k, p, q, g), n in sorted(groups.items())]))
    older = ids
print(json.dumps(results, separators=(",", ":")))
"""

def inspect(host):
    result = ssh(host, shlex.join(["python3", "-"]), payload=PROBE.encode(), timeout=100)
    return dict(host=host, samples=json.loads(result))

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as pool:
        print(json.dumps(dict(read_only=True, hosts=list(pool.map(inspect, HOSTS))), separators=(",", ":")))
