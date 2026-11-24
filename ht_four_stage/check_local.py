"""Check each local SST phase against the rank-tagged CPU golden."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["check"])
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--actual", type=Path, required=True)
args = parser.parse_args()
phase = json.loads((args.output / "manifest.json").read_text())["phase"]
stats = {"ranks": 0, "returned": 0, "completed": 0, "output": 0, "status": 0}
for rank in range(64):
    name = f"rank{rank:02d}"
    for field, width in (("returned", 4), ("completed", 512), ("output", 4), ("status", 4)):
        actual = (args.actual / name / f"{field}.bin").read_bytes()
        if field == "status":
            expected = (bytes(32) + b"\xa5" * 480) if phase == 1 else b"\xa5" * 512
        else:
            expected = (args.output / name / f"golden-{field}.bin").read_bytes()
        assert len(actual) == len(expected)
        if actual != expected:
            for offset in range(0, len(expected), width):
                if actual[offset:offset+width] != expected[offset:offset+width]:
                    raise ValueError(f"{name} {field} {offset//width} "
                                     f"{actual[offset:offset+8].hex()} != {expected[offset:offset+8].hex()}")
        stats[field] += 8 if field == "status" and phase == 1 else len(expected) // width
    stats["ranks"] += 1
print(json.dumps(stats, separators=(",",":")))
