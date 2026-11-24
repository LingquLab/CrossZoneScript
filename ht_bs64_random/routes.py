"""Deterministic random expert routes and independent FP32 reference values."""
import random
import struct
from collections import defaultdict

WORLD, LOCAL, TOKENS, HIDDEN, TOPK, EXPERTS = 64, 8, 64, 128, 6, 256
CHUNKS, ROW_BYTES, SEED, MAGIC = 4, 512, 20261124, 2026


def row(tag):
    source, token, slot, expert = tag
    return tuple((1 + source * 5 + token * 7 + slot * 11 + expert * 3 + h * 13) % 1021 / 128
                 for h in range(HIDDEN))


def floats(values):
    return struct.pack(f'<{len(values)}f', *values)


def add_rows(tags):
    return floats(tuple(map(sum, zip(*(row(tag) for tag in tags)))))


def build_routes():
    rng = random.Random(SEED)
    inputs = [[] for _ in range(WORLD)]
    gather = [[] for _ in range(WORLD)]
    forward = [defaultdict(list) for _ in range(WORLD)]
    returns = [[] for _ in range(WORLD)]
    backward = [defaultdict(list) for _ in range(WORLD)]
    original = [[] for _ in range(WORLD)]
    for source in range(WORLD):
        for token in range(TOKENS):
            by_server = defaultdict(list)
            tags = [(source, token, slot, expert) for slot, expert in enumerate(rng.sample(range(EXPERTS), TOPK))]
            original[source].append(tags)
            for tag in tags:
                expert_rank = tag[3] // (EXPERTS // WORLD)
                by_server[expert_rank // LOCAL].append((expert_rank, len(inputs[expert_rank]), tag))
                inputs[expert_rank].append(tag)
            for server, contributors in by_server.items():
                if server == source // LOCAL:
                    primary_index = next((i for i, (rank, _, _) in enumerate(contributors) if rank == source), 0)
                    contributors.insert(0, contributors.pop(primary_index))
                gateway, primary, _ = contributors[0]
                secondary = []
                for sender, input_row, tag in contributors[1:]:
                    gather_row = -1
                    if sender != gateway:
                        gather_row = len(gather[gateway])
                        gather[gateway].append(tag)
                        backward[sender][gateway % LOCAL, token // 16].append((source, input_row, gather_row))
                    secondary.append((sender, input_row, gather_row))
                forward[gateway][source, token // 16].append((token, primary, secondary))
                returns[gateway].append((source, token, primary, tuple(tag for _, _, tag in contributors)))
    return inputs, gather, forward, returns, backward, original


def local_tables(routes, rank, capacity):
    _, _, forward, _, backward, _ = routes
    records = bytearray(WORLD * capacity * 24)
    ranges = [0] * (WORLD * CHUNKS * 2)
    masks = [0] * CHUNKS
    for source in range(WORLD):
        cursor = 0
        for chunk in range(CHUNKS):
            begin = cursor
            for token, primary, secondary in forward[rank][source, chunk]:
                pieces = secondary or [(-1, -1, -1)]
                end = cursor + len(pieces)
                for sender, input_row, gather_row in pieces:
                    struct.pack_into('<6i', records, (source * capacity + cursor) * 24,
                                     end, token, primary, sender, input_row, gather_row)
                    if gather_row >= 0:
                        masks[chunk] |= 1 << (sender % LOCAL)
                    cursor += 1
            index = (source * CHUNKS + chunk) * 2
            ranges[index:index+2] = begin, cursor
    return records, struct.pack('<512i', *ranges), struct.pack('<4i', *masks), backward[rank]
