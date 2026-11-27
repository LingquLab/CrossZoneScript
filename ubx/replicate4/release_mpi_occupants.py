"""Terminate only dedicated MPI launchers for the verified NPU occupants."""
import json
import shlex

from benchmark import ssh

PROBE = r"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

def occupants(device):
    result = subprocess.check_output(["npu-smi", "info", "-t", "proc-mem", "-i", str(device)],
                                     text=True, timeout=10)
    assert re.search(rf"NPU ID\s*:\s*{device}\b", result), f"unrecognized NPU {device}"
    pids = {int(pid) for pid in re.findall(r"Process id\s*:\s*(\d+)", result, re.I)}
    assert pids or "No process in device" in result, f"unrecognized occupancy for NPU {device}"
    return pids

def process(pid):
    folder = Path("/proc") / str(pid)
    try:
        stat = (folder / "stat").read_text().rsplit(") ", 1)[1].split()
        program = Path(os.readlink(folder / "exe")).name
        return int(stat[1]), stat[19], program
    except FileNotFoundError:
        return None

def root_launchers(pids):
    programs = {"alltoall_test", "reduce_scatter_test"}
    launchers = {}
    for pid in pids:
        worker = process(pid)
        if worker is None or worker[2] not in programs:
            continue
        proxy = process(worker[0])
        if proxy is None or proxy[2] != "hydra_pmi_proxy":
            continue
        launcher_pid = proxy[0]
        launcher = process(launcher_pid)
        if launcher and launcher[2] in {"mpirun", "mpiexec", "mpiexec.hydra"}:
            launchers[launcher_pid] = launcher, worker[2]
    return launchers

signal_counts = {}
observations = []
with ThreadPoolExecutor(max_workers=8) as pool:
    for attempt in range(3):
        per_card = list(pool.map(occupants, range(16)))
        pids = set.union(set(), *per_card)
        observations.append(dict(busy_devices=[i for i, busy in enumerate(per_card) if busy],
                                 observed_processes=len(pids)))
        if not pids:
            break
        for pid, (identity, name) in root_launchers(pids).items():
            if process(pid) == identity:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    continue
                signal_counts[name] = signal_counts.get(name, 0) + 1
        time.sleep(3)
    else:
        per_card = list(pool.map(occupants, range(16)))
        observations.append(dict(busy_devices=[i for i, busy in enumerate(per_card) if busy],
                                 observed_processes=len(set.union(set(), *per_card))))

print(json.dumps(dict(signaled_mpi_launchers=signal_counts, observations=observations), separators=(",", ":")))
"""

def release(host):
    raw = ssh(host, shlex.join(["python3", "-"]), payload=PROBE.encode(), timeout=100,
              stage="dedicated MPI task cleanup")
    return json.loads(raw)

if __name__ == "__main__":
    print(json.dumps(release("90.90.93.84"), separators=(",", ":")))
