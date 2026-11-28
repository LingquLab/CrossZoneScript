"""Read the retained sender exports and check every rank after the first error."""
import json
from pathlib import Path
import sys

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here.parent / "alltoall"))
from ssh_transport import run

source = r"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

case = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/"
            "ht-scheme-a-0b9c3322-bs32-c1-8-8-ServerStaggered")
layout = json.loads((case / "export-layout.json").read_text())["ranks"]
assert len(layout) == 64
fields = ("returned", "completed", "output", "status")
mismatches = {name: [] for name in fields}
digests = {name: hashlib.sha256() for name in fields}
die_status = Counter()
seen = Counter()
status_trace = {}
for row in layout:
    rank = row["rank"]
    rank_name = f"rank{rank:02d}"
    for name in fields:
        observed = (case / "actual" / rank_name / f"{name}.bin").read_bytes()
        expected = (case / "fixture-data" / rank_name / f"golden-{name}.bin").read_bytes()
        assert len(observed) == len(expected)
        seen[name] += len(observed)
        digests[name].update(observed)
        if observed != expected:
            mismatches[name].append(rank)
    target = row["exports"]["status"]["addr"] - row["sys_va_base_addr"]
    values = []
    for die in (1, 2):
        data = (case / "bin" / f"david_{rank}_die{die}_status.bin").read_bytes()
        value, = [struct.unpack_from("<I", data, offset + 8 + target - base)[0]
                  for offset in range(0, len(data), 520)
                  if (base := struct.unpack_from("<Q", data, offset)[0]) <= target < base + 512]
        values.append(value)
    die_status[",".join(map(str, values))] += 1
    trace = case / "OUTPUT/stat_trace" / f"Host{rank}_ba_layer_trace.json"
    if rank in mismatches["status"] and len(status_trace) < 8:
        status_trace[str(rank)] = {"dies": values, "trace_exists": trace.is_file()}
        if trace.is_file():
            hits = [match.group(0) for match in re.finditer(rb'\{[^{}]{1,1200}\}', trace.read_bytes())
                    if b'"oriAddr_"' in match.group(0) and any(x in match.group(0) for x in (b'WR_REQ', b'WR_DATA'))]
            for hit in hits:
                args = json.loads(hit)
                raw_addr = args["oriAddr_"]
                address = int(raw_addr, 0) if isinstance(raw_addr, str) else raw_addr
                if address in (target, row["exports"]["status"]["addr"]):
                    status_trace[str(rank)]["write_records"] = status_trace[str(rank)].get("write_records", 0) + 1
print(json.dumps(dict(bytes_checked=seen, mismatched_ranks=mismatches, status_dies=die_status, samples=status_trace,
                      exports_sha256={name: digest.hexdigest() for name, digest in digests.items()}), separators=(",", ":")))
"""
print(json.dumps(run(source, password=True, timeout=90), separators=(",", ":")))
