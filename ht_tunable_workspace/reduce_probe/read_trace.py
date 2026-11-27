"""Read already captured per-instruction rows and report first differing lanes."""
import json
from pathlib import Path
import sys

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here.parents[1] / "alltoall"))
from ssh_transport import run

options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
source = r"""
import json
from pathlib import Path
import re
import struct
import sys

root = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/cast-v310-repair-0784b139")
options = json.load(sys.stdin)
label = options.get("label", "instructions-v1")
assert re.fullmatch(r"[a-z0-9-]+", label)
reports = {}
for role in ("gateway", "final"):
    case = root / ("reduce-probe-" + label) / role
    actual = case / "actual-trace.bin"
    if not actual.is_file():
        continue
    observed = actual.read_bytes()
    trace = json.loads((case / "check.json").read_bytes())["trace"]
    per_step = {}
    for step, layout in trace.items():
        offset, size, dtype = (layout[key] for key in ("offset", "size", "dtype"))
        expected = (case / ("golden-trace-" + step + ".bin")).read_bytes()
        fmt = f"<{size // struct.calcsize(dtype)}{dtype}"
        got_values = struct.unpack(fmt, observed[offset:offset+size])
        want_values = struct.unpack(fmt, expected)
        bad = [i for i, (got, want) in enumerate(zip(got_values, want_values)) if got != want]
        if not bad:
            continue
        def value(raw):
            return raw if dtype == "f" else struct.unpack("<f", struct.pack("<I", raw << 16))[0]

        per_step[step] = {"bad_per_64": [sum(i // 64 == group for i in bad) for group in range(4)],
                          "examples": [[i, value(want_values[i]), value(got_values[i])] for i in bad[:3]]}
    reports[role] = per_step
print(json.dumps(reports, separators=(",", ":")))
"""
print(json.dumps(run(source, data=options, password=True, timeout=75), separators=(",", ":")))
