"""FP32 routes shared by gateway reduction and both return roles."""
import struct

WORLD = 64
LOCAL = 8
TOPK = 8


def build_routes(rank, row_values, tokens=4, chunks=4, chunk_ends=None):
    if chunk_ends is None:
        assert tokens % chunks == 0
        chunk_ends = [tokens * (index + 1) // chunks for index in range(chunks)]
    assert len(chunk_ends) == chunks and chunk_ends[-1] == tokens
    chunk_ranges = list(zip([0] + chunk_ends[:-1], chunk_ends))
    assert all(first < end for first, end in chunk_ranges)
    capacity = tokens * TOPK
    local = rank % LOCAL
    server = rank // LOCAL
    other_local = server * LOCAL + (local + 1) % LOCAL
    other_server = rank ^ LOCAL
    values = [float(row_values(server, local, token, h)) for token in range(tokens) for h in range(128)]
    input_image = struct.pack(f"<{2 * tokens * 128}f", *(values + values))
    forward = bytearray(WORLD * capacity * 24)
    padded = bytearray(WORLD * capacity * 32)
    counts = [0] * WORLD
    ranges = [0] * (WORLD * chunks * 2)
    contributors = [sender for sender in range(LOCAL) if sender != local]
    destinations = ((rank, 0, range(0, tokens, 2)),
                    (other_local, 0, range(1, tokens, 2)),
                    (other_server, 1, range(tokens)))
    for peer, lane, selected in destinations:
        for chunk, (first, end_token) in enumerate(chunk_ranges):
            begin = counts[peer]
            for token in range(first, end_token):
                if token not in selected:
                    continue
                row = counts[peer]
                end = row + len(contributors)
                for index, sender in enumerate(contributors, row):
                    record = (end, token, token + lane * tokens, server * LOCAL + sender,
                              token, sender * tokens + token)
                    offset = peer * capacity + index
                    struct.pack_into("<6i", forward, offset * 24, *record)
                    struct.pack_into("<6i", padded, offset * 32, *record)
                counts[peer] = end
            pos = (peer * chunks + chunk) * 2
            ranges[pos:pos+2] = begin, counts[peer]
    backward_rows = []
    backward_counts = []
    backward_ranges = []
    for gateway in range(LOCAL):
        rows = []
        for first, end_token in chunk_ranges:
            begin = len(rows) // 3
            if gateway != local:
                for token in range(first, end_token):
                    rows.extend((server * LOCAL + gateway, token, local * tokens + token))
            backward_ranges.extend((begin, len(rows) // 3))
        backward_rows.append(rows)
        backward_counts.append(len(rows) // 3)
    return dict(input=input_image, forward=bytes(forward), return_forward=bytes(padded),
                counts=struct.pack("<64i", *counts), ranges=struct.pack(f"<{WORLD * chunks * 2}i", *ranges),
                masks=struct.pack(f"<{chunks}i", *([255 ^ (1 << local)] * chunks)),
                server_mask=struct.pack(f"<{tokens}Q", *([(1 << server) | (1 << (other_server // LOCAL))] * tokens)),
                backward_rows=backward_rows, backward_counts=backward_counts, backward_ranges=backward_ranges)


if __name__ == "__main__":
    for tokens in (4, 8, 16, 32):
        for rank in range(WORLD):
            data = build_routes(rank, lambda server, local, token, h: server * 64 + local * 8 + token + h / 64,
                                tokens)
            counts = struct.unpack("<64i", data["counts"])
            assert sorted(count for count in counts if count) == [tokens * 7 // 2] * 2 + [tokens * 7]
            assert data["forward"] == b"".join(
                data["return_forward"][i:i+24] for i in range(0, len(data["return_forward"]), 32))
        print(f"Checked 64 rank forward tables and SST padding for T{tokens}")
