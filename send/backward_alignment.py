# Copyright (c) 2026 TileXR Project.
"""Pad backward records without changing their three fields or route ordering.

The aligned diagnostic uses four int64 slots per record in GM and UB. A local
copy of the common header selects that stride; the public kernel argument
structure and the golden payloads retain their existing layout.
"""
from pathlib import Path


def write_common_header(folder, baseline, record_bytes):
    assert type(record_bytes) is int and record_bytes in (24, 32)
    if record_bytes == 24:
        return
    source = Path(baseline) / "candidate/src/moonep/combine_ht/common/combine_ht.h"
    text = source.read_text()
    assert text.count("kBackwardFields = 3") == 1
    text = text.replace("kBackwardFields = 3", "kBackwardFields = 4")
    (Path(folder) / "combine_ht.h").write_text(text)


def repack(prepared, record_bytes):
    assert type(record_bytes) is int and record_bytes in (24, 32)
    output, ranks = [], []
    for images, golden_send, golden_reduce in prepared:
        original = images["backward"]
        assert len(original) % 24 == 0
        count = len(original) // 24
        aligned = original
        if record_bytes == 32:
            aligned = bytearray(count * 32)
            for row in range(count):
                aligned[row * 32:row * 32 + 24] = original[row * 24:row * 24 + 24]
            aligned = bytes(aligned)
        output.append(({**images, "backward": aligned}, golden_send, golden_reduce))
        ranks.append({"records": count, "original_bytes": len(original), "prepared_bytes": len(aligned)})
    return output, {"record_bytes": record_bytes, "fields": record_bytes // 8, "ranks": ranks}
