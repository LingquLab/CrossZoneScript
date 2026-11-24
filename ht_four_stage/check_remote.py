"""Check inter-server data and final mixed-server outputs against independent CPU golden."""
import argparse
import json
from pathlib import Path
import struct

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["check"])
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--actual", type=Path, required=True)
args = parser.parse_args()
phase = json.loads((args.output / "manifest.json").read_text())["phase"]
stats = {"ranks": 0, "returned": 0, "completed": 0, "output": 0, "status": 0}
for rank in range(64):
    name = f"rank{rank:02d}"
    for field, width in (("returned", 4), ("completed", 512), ("output", 4), ("status", 32)):
        actual = (args.actual / name / f"{field}.bin").read_bytes()
        source = args.output / name / f"golden-{field}.bin"
        expected = (bytes(4) + b"\xa5" * 28) * 16 if field == "status" else source.read_bytes()
        assert len(actual) == len(expected)
        if actual != expected:
            for offset in range(0, len(expected), width):
                if actual[offset:offset+width] != expected[offset:offset+width]:
                    raise ValueError(f"{name} {field} {offset//width} "
                                     f"{actual[offset:offset+8].hex()} != {expected[offset:offset+8].hex()}")
        stats[field] += len(expected) // width
    stats["ranks"] += 1
if phase == 4:
    rank = args.actual / "rank00"
    returns = (rank / "returned.bin").read_bytes()
    output = (rank / "output.bin").read_bytes()
    stats["rank0_token0_h0"] = dict(local=struct.unpack_from("<f", returns, 0)[0],
                                      remote=struct.unpack_from("<f", returns, 512)[0],
                                      mixed=struct.unpack_from("<f", output, 0)[0])
print(json.dumps(stats, separators=(",", ":")))
