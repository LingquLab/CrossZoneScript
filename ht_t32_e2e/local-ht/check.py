"""Check live intranode URMA gather, progress flags, and FP32 reduction."""
import argparse
import json
import struct
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["check"])
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--actual", type=Path, required=True)
args = parser.parse_args()
stats = dict(ranks=0, returned_values=0, completion_slots=0, output_values=0, statuses=0)
for rank in range(64):
    name = f"rank{rank:02d}"
    for field, width, counter in (("returned", 4, "returned_values"), ("completed", 512, "completion_slots"),
                                  ("output", 4, "output_values")):
        actual = (args.actual / name / f"{field}.bin").read_bytes()
        expected = (args.output / name / f"golden-{field}.bin").read_bytes()
        assert len(actual) == len(expected)
        for offset in range(0, len(actual), width):
            if actual[offset:offset+width] != expected[offset:offset+width]:
                raise ValueError(f"{name} {field}[{offset//width}] mismatch: "
                                 f"{actual[offset:offset+4].hex()} != {expected[offset:offset+4].hex()}")
        stats[counter] += len(actual) // width
    raw = (args.actual / name / "status.bin").read_bytes()
    for core, (status,) in enumerate(struct.iter_unpack("<I", raw[:32])):
        if status != 0:
            raise ValueError(f"{name} status[{core}] mismatch: {status:08x} != 00000000")
    assert raw[32:] == b"\xa5" * (512-32)
    stats["statuses"] += 8
    stats["ranks"] += 1
print(json.dumps(stats,separators=(",",":")))
