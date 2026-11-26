"""Identify the route communicator in which MR registration failed."""
from concurrent.futures import ThreadPoolExecutor
import json

from benchmark import HOSTS, ssh

PROBE = r"""
from pathlib import Path
import json
import re

root = Path("/root/ubx-replicate4-e172cd2-bs32-f50/full-bs32-f50")
failures = []
bind_errors = []
for path in sorted(root.glob("rank*/worker.log")):
    rank = int(path.parent.name[4:])
    lines = path.read_text(errors="replace").splitlines()
    bind_errors.extend(line.strip()[:150] for line in lines if re.search(
        r"Server side bind \d+ failed|Address already in use", line
    ))
    for index, line in enumerate(lines):
        if not line.strip().startswith("route MR registration:"):
            continue
        previous = lines[:index]
        initialized = sum("TileXRCommInit " in row and " success." in row for row in previous)
        failures.append(dict(
            rank=rank, error=line.strip(),
            route_communicators_created=initialized - (4 if rank % 16 == 0 else 3),
            endpoints=[row.strip()[:175] for row in previous
                       if "TILEXR_COMM_ID set" in row or "The server is listening!" in row][-4:],
            provider_errors=[row.strip()[:190] for row in previous
                             if "MR registration failed errno=" in row or "halMemRegUbSegment" in row][-3:],
        ))
print(json.dumps(dict(route_registration_failures=failures, bind_errors=bind_errors[:5])))
"""


def read(host):
    return dict(host=host, **json.loads(ssh(host, "python3 -", payload=PROBE.encode())))


with ThreadPoolExecutor(max_workers=2) as pool:
    print(json.dumps(dict(read_only=True, hosts=list(pool.map(read, HOSTS))),
                     separators=(",", ":")))
