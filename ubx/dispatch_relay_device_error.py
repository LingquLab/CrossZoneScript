"""Correlate bounded CANN device error classes with the failed RELAY cases."""

from concurrent.futures import ThreadPoolExecutor
import json
import re
from pathlib import Path, PurePosixPath
import shlex
import sys

from dispatch_relay_setup import NAMESPACE
from stage_sources import connect, run_remote

PROBE = r'''
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
patterns = {
    "vector_exception": r"(?:vector|aiv).*?(?:error|exception|fault)",
    "vector_address": r"(?:vec\w*|vector).*?(?:address|addr).*?(?:out of|invalid|unaligned|align|overflow)",
    "ub_address": r"(?:ub |ubuf|unified buffer).*?(?:address|addr|overflow|bounds|align)",
    "unaligned": r"unalign|not align|misalign",
    "mte": r"mte.*?(?:error|exception|out of|bound|align)",
    "bounds": r"out.of.bounds|out.of.range|out of bound|address range",
    "instruction": r"illegal instruction|invalid instruction|unsupported instruction",
    "aicore_timeout": r"(?:aicore|aiv).{0,100}timeout",
}
rule = {name: re.compile(pattern, re.I) for name, pattern in patterns.items()}
stamp = re.compile(r"20\d\d-\d\d-\d\d[- T]\d\d:\d\d:\d\d")
kernel = re.compile(r"tilexr_ep_ubx(?:_barrier)?_kernel\w*")
hardware = re.compile(r"(?:exception code|error code|error code is|mte error info|vec error info)\s*[:=]?\s*(0x[0-9a-f]+)", re.I)
cases = {}
for variant in ("vector", "align"):
    path = root / "runs" / f"tail-relay-{variant}-dispatch-bs64-max2-off"
    files = list(path.glob("rank-*/stderr.log"))
    if files:
        times = [entry.stat().st_mtime for entry in files]
        cases[variant] = min(times) - 25, max(times) + 5
results = {name: dict(classes=Counter(), kernels=Counter(), hardware=Counter())
           for name in cases}
scanned = 0
for base in (Path.home() / "ascend/log/run", Path.home() / "ascend/log/plog", Path.home() / "ascend/log/debug"):
    for path in base.glob("**/plog*.log"):
        try:
            info = path.stat()
            if not path.is_file() or not any(low - 90 <= info.st_mtime <= high + 150
                                             for low, high in cases.values()):
                continue
            scanned += 1
            with path.open("rb") as stream:
                stream.seek(max(0, info.st_size - 400000))
                lines = stream.read().decode(errors="replace").splitlines()
        except OSError:
            continue
        if scanned > 200:
            break
        for line in lines:
            part = stamp.search(line)
            if part is None:
                continue
            formatted = part[0][:10] + " " + part[0][11:]
            try:
                when = datetime.fromisoformat(formatted).timestamp()
            except ValueError:
                continue
            for name, (low, high) in cases.items():
                if not low <= when <= high:
                    continue
                result = results[name]
                entry = kernel.search(line)
                if entry:
                    result["kernels"][entry[0]] += 1
                for label, pattern in rule.items():
                    if pattern.search(line):
                        result["classes"][label] += 1
                if entry or "aicore" in line.lower() or "aiv" in line.lower():
                    code = hardware.search(line)
                    if code:
                        result["hardware"][code[1]] += 1
print(json.dumps(dict(scanned_plogs=scanned, cases=results), separators=(",", ":")))
'''


def inspect(host):
    with connect(host) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / NAMESPACE
        code, output, errors = run_remote(client, "/usr/bin/python3 - " + shlex.quote(str(root)),
                                          timeout=85, input_text=PROBE)
        if code:
            kind = re.search(r"(?m)^([A-Z][A-Za-z]+(?:Error|Exception)):", errors)
            return dict(host=host, exit_code=code, error=kind[1] if kind else "unclassified")
    return dict(host=host, **json.loads(output))


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = {host: pool.submit(inspect, host) for host in args["hosts"]}
        for host, result in results.items():
            try:
                report = result.result()
            except (OSError, ValueError, RuntimeError) as error:
                report = dict(host=host, error=type(error).__name__)
            print(json.dumps(report, separators=(",", ":")))
