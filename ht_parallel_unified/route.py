"""FP32 T4 routes shared by gateway reduction and both return roles."""
import struct

WORLD = 64
LOCAL = 8
TOKENS = 4
TOPK = 8
FORWARD_CAPACITY = TOKENS * TOPK
GATHER_ROWS = LOCAL * TOKENS
INPUT_ROWS = PARTIAL_ROWS = 2 * TOKENS


def build_routes(rank, row_values):
    local = rank % LOCAL
    server = rank // LOCAL
    other_local = server * LOCAL + (local + 1) % LOCAL
    other_server = rank ^ LOCAL
    values = [float(row_values(server, local, token, h)) for token in range(TOKENS) for h in range(128)]
    input_image = struct.pack("<1024f", *(values + values))
    forward = bytearray(WORLD * FORWARD_CAPACITY * 24)
    padded = bytearray(WORLD * FORWARD_CAPACITY * 32)
    counts = [0] * WORLD
    ranges = [0] * (WORLD * TOKENS * 2)
    for peer, lane, selected in ((rank, 0, (0, 2)), (other_local, 0, (1, 3)), (other_server, 1, range(TOKENS))):
        for token in range(TOKENS):
            begin = counts[peer]
            end = begin + (LOCAL - 1) * (token in selected)
            pos = (peer * TOKENS + token) * 2
            ranges[pos:pos+2] = begin, end
            counts[peer] = end
            if token not in selected:
                continue
            secondary = [sender for sender in range(LOCAL) if sender != local]
            for row, sender in enumerate(secondary, begin):
                values = end, token, token + lane * TOKENS, server * LOCAL + sender, token, sender * TOKENS + token
                index = peer * FORWARD_CAPACITY + row
                struct.pack_into("<6i", forward, index * 24, *values)
                struct.pack_into("<6i", padded, index * 32, *values)
    backward_rows = []
    backward_counts = []
    backward_ranges = []
    for gateway in range(LOCAL):
        rows = []
        for token in range(TOKENS):
            begin = len(rows) // 3
            if gateway != local:
                rows.extend((server * LOCAL + gateway, token, local * TOKENS + token))
            backward_ranges.extend((begin, len(rows) // 3))
        backward_rows.append(rows)
        backward_counts.append(len(rows) // 3)
    return dict(input=input_image, forward=bytes(forward), return_forward=bytes(padded),
                counts=struct.pack("<64i", *counts), ranges=struct.pack("<512i", *ranges),
                masks=struct.pack("<4i", *([255 ^ (1 << local)] * TOKENS)),
                server_mask=struct.pack("<4Q", *([(1 << server) | (1 << (other_server//LOCAL))] * TOKENS)),
                backward_rows=backward_rows, backward_counts=backward_counts, backward_ranges=backward_ranges)


if __name__ == "__main__":
    for rank in range(WORLD):
        data = build_routes(rank, lambda server, local, token, h: server * 64 + local * 8 + token + h / 64)
        counts = struct.unpack("<64i", data["counts"])
        assert sorted(count for count in counts if count) == [14, 14, 28]
        assert data["forward"] == b"".join(data["return_forward"][i:i+24] for i in range(0, len(data["return_forward"]), 32))
    print("Checked 64 rank forward tables and SST padding for T4")
