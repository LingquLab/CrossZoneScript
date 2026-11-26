"""Retry the same benchmark on a different port only for a logged bind failure."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import benchmark

PROBE = r"""
from pathlib import Path
import json
import re

output = Path("/root/ubx-replicate4-e172cd2-bs32-f50/full-bs32-f50")
errors = []
for path in sorted(output.glob("rank*/worker.log")):
    for line in path.read_text(errors="replace").splitlines():
        if re.search(r"Server side bind \d+ failed|Address already in use", line):
            errors.append(dict(rank=path.parent.name, line=line[:210]))
ephemeral = [int(value) for value in Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()]
print(json.dumps(dict(bind_errors=errors[:4], ephemeral=ephemeral,
                      rank_records=len(list(output.glob("rank*/rounds.jsonl"))))))
"""


def read(host):
    value = json.loads(benchmark.ssh(host, "python3 -", payload=PROBE.encode()))
    return {"host": host, **value}


def main():
    prior = Path.cwd() / "runs/replicate4-bs32-50-procfs-cleanup/1/execution/execution.json"
    receipt = json.loads(prior.read_text())
    assert receipt["status"] == "EXITED" and receipt["exit_code"] == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        source = list(pool.map(read, benchmark.HOSTS))
    if not any(host["bind_errors"] for host in source):
        print(json.dumps({"retried": False, "reason": "no logged TCP bind error", "hosts": source}))
        return
    assert all(not host["rank_records"] for host in source)
    ports = tuple(port for port in (24500, 26000, 28000, 61000)
                  if all(port + 513 < host["ephemeral"][0] or port > host["ephemeral"][1]
                         for host in source))
    assert ports, "No candidate range outside both ephemeral port intervals"
    benchmark.OUTPUT = benchmark.ROOT / "full-bs32-f50-bootstrap-port-retry"
    benchmark.main(ports=ports, cleanup=True)


if __name__ == "__main__":
    main()
