"""Read the packet cap recorded by each completed Dispatch rank and round."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys

from stage_sources import connect, run_remote


PROBE = r'''
from collections import Counter
import json
from pathlib import Path
import sys

base = Path(sys.argv[1])
result = {}
for batch in (64, 128):
    for mode in ("off", "on"):
        name = f"dispatch-dispatch-bs{batch}-{mode}"
        case = base / "runs" / name
        files = sorted(case.glob("rank-*/*.summary.json"))
        caps = Counter()
        for file in files:
            sample = json.loads(file.read_text())
            if sample["batch"] != batch or sample["dtype"] != "fp8e4m3" or not sample["correctness"]:
                raise ValueError("unexpected prior run identity")
            caps[sample["max_tokens"]] += 1
        if sum(caps.values()) != 400:
            raise ValueError("incomplete host evidence")
        result[name] = dict(caps)
print(json.dumps(result))
'''


def inspect(host, namespace):
    with connect(host) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / namespace
        invocation = "/usr/bin/python3 - " + shlex.quote(str(root))
        rc, out, _ = run_remote(client, invocation, input_text=PROBE)
        if rc:
            raise RuntimeError("prior Dispatch results unavailable")
        return {"host": host, **json.loads(out)}


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args["namespace"]):
        raise ValueError("invalid namespace")
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(lambda host: inspect(host, args["namespace"]), args["hosts"]):
            print(json.dumps(result, separators=(",", ":")))
