"""Summarize first rank-level failures and retained controls for the RELAY matrix."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path, PurePosixPath
import shlex
import sys

from dispatch_relay_setup import NAMESPACE
from stage_sources import connect, run_remote

PROBE = r'''
from collections import Counter
import json
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
cases = json.loads((root / "matrix.json").read_text())
results = {}
for variant, operator, batch, mode, tokens in cases:
    name = f"{variant}-{operator}-bs{batch}-max{tokens}-{mode}"
    folder = root / "runs" / name
    if not folder.is_dir():
        continue
    if variant in ("tail-relay-base", "tail-relay-ub"):
        timing = folder / "timing.json"
        if timing.is_file():
            doc = json.loads(timing.read_text())
            results[name] = dict(us=doc["native_task_us"]["round_slowest_rank"])
        continue
    errors, core_errors = Counter(), Counter()
    for rank in sorted(folder.glob("rank-*")):
        for filename in ("stdout.log", "stderr.log"):
            log = rank / filename
            if not log.is_file():
                continue
            with log.open(errors="replace") as stream:
                for line in stream:
                    failure = re.search(r"UBX_FAILURE rank=\d+ error=([^;\r\n]{1,180})", line)
                    if failure:
                        errors[failure[1]] += 1
                    bad = re.search(r"(?:core|role)=\d+.*?status=-(?:[1-9]\d*)\b.*?phase=\d+", line)
                    if bad:
                        core_errors[bad[0]] += 1
    results[name] = dict(summaries=len(list(folder.glob("rank-*/*.summary.json"))),
                         errors=errors.most_common(3), core_errors=core_errors.most_common(2))
print(json.dumps(results, separators=(",", ":")))
'''


def inspect(host):
    with connect(host) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / NAMESPACE
        cmd = "/usr/bin/python3 - " + shlex.quote(str(root))
        status, output, _ = run_remote(client, cmd, input_text=PROBE)
        if status:
            raise RuntimeError("missing RELAY evidence")
    return dict(host=host, cases=json.loads(output))


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        for report in pool.map(inspect, args["hosts"]):
            print(json.dumps(report, separators=(",", ":")))
