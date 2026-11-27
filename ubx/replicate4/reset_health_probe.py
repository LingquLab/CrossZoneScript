"""Read-only checks when the NPU management CLI does not answer after reset."""
import json
from pathlib import Path

from benchmark import ssh

PROBE = r"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import time

def command(argv, timeout):
    started = time.monotonic()
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or b"").decode(errors="replace")
        stderr = (error.stderr or b"").decode(errors="replace")
        exit_code = None
    else:
        output, stderr, exit_code = result.stdout, result.stderr, result.returncode
    return dict(exit_code=exit_code, seconds=round(time.monotonic() - started, 1), output=output, stderr=stderr)

def probe(query, device):
    result = command(["npu-smi", "info", "-t", query, "-i", str(device)], 25)
    data = result.pop("output")
    error = result.pop("stderr")
    pids = re.findall(r"Process id\s*:\s*(\d+)", data, re.I)
    status = re.search(r"Health Status\s*:\s*(\w+)", data)
    codes = re.search(r"Error Code\s*:\s*([^\r\n]+)", data)
    return dict(device=device, **result, health=status[1] if status else None,
                idle="No process in device" in data, process_count=len(pids),
                codes=codes[1].split() if codes else [], detail=(error or data).splitlines()[-2:] if not (pids or status or "No process in device" in data) else [])

base = command(["npu-smi", "info", "-l"], 30)
base["output"] = [line.strip()[:140] for line in base["output"].splitlines()[:6]]
base["stderr"] = base["stderr"].splitlines()[-2:]
logs = command(["dmesg", "--since", "30 minutes ago"], 10)
kernel = [line[-240:] for line in logs["output"].splitlines()
          if re.search(r"reset.*(fail|timeout)|80CB8001|hisi.*(error|timeout)|task.*blocked for", line, re.I)]
processes = Counter()
for stat in Path("/proc").glob("[0-9]*/stat"):
    try:
        data = stat.read_text()
        fields = data.rsplit(") ",1)[1].split()
    except (OSError, IndexError):
        continue
    name = data.split("(", 1)[1].rsplit(")",1)[0]
    if name.startswith(("npu-smi", "alltoall_test", "reduce_scatter")):
        processes[name, fields[0]] += 1
with ThreadPoolExecutor(max_workers=2) as pool:
    devices = list(pool.map(lambda pair: probe(*pair), [("health", 3), ("proc-mem", 3), ("health", 15), ("proc-mem", 15)]))
print(json.dumps(dict(visible=base, devices=devices, kernel_tail=kernel[-6:],
                      processes=[list(key)+[count] for key, count in processes.items()]), separators=(",", ":")))
"""

if __name__ == "__main__":
    print(ssh("90.90.93.84", "python3 -", payload=PROBE.encode(), timeout=110).decode().strip())
