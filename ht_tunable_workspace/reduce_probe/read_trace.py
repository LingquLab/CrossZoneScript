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
            if dtype == "I":
                return hex(raw)
            return raw if dtype == "f" else struct.unpack("<f", struct.pack("<I", raw << 16))[0]

        per_step[step] = {"bad_per_64": [sum(i // 64 == group for i in bad) for group in range(4)],
                          "examples": [[i, value(want_values[i]), value(got_values[i])] for i in bad[:3]]}
    reports[role] = per_step
    raw_reports = {}
    for index in (0, 1):
        name = f"raw{index}"
        if name not in trace:
            continue
        begin = trace[name]["offset"]
        registers = struct.unpack_from("<256I", observed, begin)
        begin = trace[f"input{index}"]["offset"]
        ub = struct.unpack_from("<256H", observed, begin)
        lows = [word & 0xffff for word in registers]
        packed_matches = [sum(registers[start + lane] == (ub[start + 2 * lane] | ub[start + 2 * lane + 1] << 16)
                              for lane in range(32)) for start in range(0, 256, 64)]
        raw_reports[name] = {
            "wrong_low_half": sum(got != want for got, want in zip(lows, ub)),
            "low_half_vs_unpacked_mismatches": sum(got != want for got, want in zip(
                lows, struct.unpack_from("<256H", observed, trace[f"unpack{index}"]["offset"]))),
            "normal_packed_matches_per_32": packed_matches,
            "samples": [[i, hex(ub[i]), hex(registers[i])] for i in (4, 5, 9, 31, 32, 64, 69)],
        }
    if raw_reports:
        reports[role]["register_pattern"] = raw_reports
print(json.dumps(reports, separators=(",", ":")))
"""
print(json.dumps(run(source, data=options, password=True, timeout=75), separators=(",", ":")))
