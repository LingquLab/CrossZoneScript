"""Check transport output, completion flags and reduced values for every rank."""
import argparse
import json
from pathlib import Path
import struct

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["check"])
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--actual", type=Path, required=True)
args = parser.parse_args()
stats = dict(ranks=0, returned_values=0, completion_slots=0, output_values=0, statuses=0)
for rank in range(64):
    name = f'rank{rank:02d}'
    expected = args.output / name
    received = args.actual / name
    for field, width, counter in (("returned", 4, "returned_values"), ("completed", 512, "completion_slots"),
                                  ("output", 4, "output_values")):
        actual = (received / f'{field}.bin').read_bytes()
        golden = (expected / f'golden-{field}.bin').read_bytes()
        assert len(actual) == len(golden), (rank, field, len(actual), len(golden))
        for at in range(0, len(golden), width):
            size = 8 if field == 'completed' else width
            if actual[at:at + size] != golden[at:at + size]:
                raise ValueError(f'{name} {field}[{at//width}] mismatch: '
                                 f'{actual[at:at+size].hex()} != {golden[at:at+size].hex()}')
        stats[counter] += len(golden) // width
    raw = (received / 'status.bin').read_bytes()
    assert len(raw) == 1024, (rank, 'status', len(raw))
    for block in range(32):
        at = block * 32
        assert struct.unpack_from('<I', raw, at)[0] == 0, (rank, 'status', block, raw[at:at+4].hex())
        assert raw[at+4:at+32] == b'\xa5'*28, (rank, 'status_padding', block)
    stats['statuses'] += 32
    stats['ranks'] += 1
print(json.dumps(stats, separators=(',', ':')))
