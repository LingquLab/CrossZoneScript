"""Measure byte mismatches and replay final summation from actual return data."""
from collections import Counter
import struct


def f32(word):
    return struct.unpack('<f', struct.pack('<I', word << 16))[0]


def bf16(value):
    bits, = struct.unpack('<I', struct.pack('<f', value))
    return (bits + 0x7fff + ((bits >> 16) & 1)) >> 16


def words(path):
    return [word for word, in struct.iter_unpack('<H', path.read_bytes())]


counts = Counter()
samples = {field: [] for field in ('partial', 'returned', 'output', 'output_from_actual_returned')}
bands = Counter()
checked = Counter()
for rank in range(64):
    name = f'rank{rank:02d}'
    raw = case / 'actual' / name
    ref = case / 'fixture-data' / name
    actual_returned = words(raw / 'returned.bin')
    for field in ('partial', 'returned', 'output'):
        actual = words(raw / (field + '.bin'))
        expected = words(ref / ('golden-' + field + '.bin'))
        pairs = {field: zip(actual, expected, strict=True)}
        if field == 'output':
            server = rank // 8
            reconstructed = [bf16(f32(actual_returned[(token * 8 + server) * 256 + col]) +
                                  f32(actual_returned[(token * 8 + (server ^ 1)) * 256 + col]))
                             for token in range(4) for col in range(256)]
            pairs['output_from_actual_returned'] = zip(actual, reconstructed, strict=True)
        for label, values in pairs.items():
            for index, (got, want) in enumerate(values):
                checked[label] += 1
                if got != want:
                    counts[label] += 1
                    bands[(label, index % 256 // 32)] += 1
                    if len(samples[label]) < 4:
                        samples[label].append([rank, index // 256, index % 256, f32(got), f32(want)])
print(json.dumps(dict(checked=checked, mismatches=counts,
                      by_column_band={key: [bands[key, i] for i in range(8)] for key in checked},
                      first=samples), separators=(',', ':')))
