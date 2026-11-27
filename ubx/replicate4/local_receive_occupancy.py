"""Identify busy devices, live process states and the last cleanup outcome."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import PurePosixPath
import shlex

from benchmark import HOSTS, ssh
from retest_local_receive import WORK

PROBE = r"""
import json
from pathlib import Path
import re
import subprocess
import sys

base = Path(sys.argv[1])
cleanup = base / "full-bs32-f50-npu-cleanup.json"
devices = []
for device in range(16):
    cmd = ["npu-smi", "info", "-t", "proc-mem", "-i", str(device)]
    response = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
    pids = set(map(int, re.findall(r"Process id\s*:\s*(\d+)", response.stdout, re.I)))
    if not pids and response.returncode == 0 and "No process in device" in response.stdout:
        continue
    counts = {}
    for pid in pids:
        try:
            state = (Path("/proc") / str(pid) / "stat").read_text().rsplit(") ", 1)[1].split()[0]
        except FileNotFoundError:
            state = "missing"
        counts[state] = counts.get(state, 0) + 1
    devices.append(dict(device=device, proc_exit=response.returncode, processes=len(pids), states=counts))
print(json.dumps(dict(cards=devices, cleanup=json.loads(cleanup.read_text()) if cleanup.is_file() else None)))
"""


def read(host):
    result = ssh(host, shlex.join(["python3", "-", str(PurePosixPath(WORK))]),
                 payload=PROBE.encode(), timeout=115)
    return dict(host=host, **json.loads(result))


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as pool:
        hosts = list(pool.map(read, HOSTS))
    print(json.dumps(dict(read_only=True, hosts=hosts), separators=(",", ":")))
