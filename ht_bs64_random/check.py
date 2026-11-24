"""Compare every exported byte with a separate contribution-ledger oracle."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['check'])
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--actual', type=Path, required=True)
args = parser.parse_args()
phase = json.loads((args.output / 'manifest.json').read_text())['phase']
counts = dict(ranks=0, returned=0, completed=0, output=0, status=0)
for rank in range(64):
    name = f'rank{rank:02d}'
    for field, width in (('returned', 4), ('completed', 512), ('output', 4), ('status', 4 if phase <= 2 else 32)):
        data = (args.actual / name / (field + '.bin')).read_bytes()
        if field != 'status':
            expected = (args.output / name / ('golden-' + field + '.bin')).read_bytes()
        elif phase == 1:
            expected = bytes(32) + b'\xa5' * 480
        elif phase == 2:
            expected = b'\xa5' * 512
        else:
            expected = (bytes(4) + b'\xa5' * 28) * 16
        assert len(data) == len(expected), (name, field, len(data), len(expected))
        if data != expected:
            first = next(offset for offset in range(0, len(expected), width)
                         if data[offset:offset+width] != expected[offset:offset+width])
            raise ValueError(f'{name} {field} {first//width} {data[first:first+8].hex()} != {expected[first:first+8].hex()}')
        counts[field] += len(expected) // width
    counts['ranks'] += 1
print(json.dumps(counts, separators=(',', ':')))
