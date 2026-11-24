# Copyright (c) 2026 TileXR Project.
"""Generate 64-source top-6 routes, HT metadata, and BF16 golden data for SST."""

from array import array
from dataclasses import dataclass
import random
import struct

SOURCES = 64
LOCAL_RANKS = 8
TOPK = 6
EXPERTS_PER_RANK = 4
GUARD = 0xC188  # BF16 -17
SLOT_BYTES = 512


def align(size, boundary=SLOT_BYTES):
    return (size + boundary - 1) // boundary * boundary


def expert_routes(tokens, seed):
    routes = []
    for source in range(SOURCES):
        rng = random.Random((seed << 32) | source)
        routes.append([rng.sample(range(SOURCES * EXPERTS_PER_RANK), TOPK) for _ in range(tokens)])
    return routes


def bf16(value):
    bits, = struct.unpack("<I", struct.pack("<f", value))
    return (bits + 0x7FFF + ((bits >> 16) & 1)) >> 16


def contribution(source, token, slot, rank):
    # Quantized multiples of 1/16 remain exact in FP32 throughout a top-16 sum.
    key = ((source * 65536 + token) * TOPK + slot) * SOURCES + rank
    return [16 + ((key >> (4 * (lane % 8))) & 15) for lane in range(16)]


def row_bytes(values, hidden):
    return struct.pack("<16H", *(bf16(value / 16) for value in values)) * (hidden // 16)


@dataclass
class Rank:
    rank: int
    tokens: int
    hidden: int
    chunk: int
    splits: int
    reducers: int

    def __post_init__(self):
        self.first = min(self.tokens, self.chunk)
        self.chunks = (self.tokens + self.chunk - 1) // self.chunk + self.splits - 1
        self.forward_capacity = self.tokens * TOPK
        self.backward_capacity = SOURCES * self.forward_capacity
        self.controls = 3 * SOURCES + 96 + self.chunks * LOCAL_RANKS + 64 + SOURCES * self.chunks * 2 + 64
        self.gather_offset = self.controls * SLOT_BYTES
        self.input_rows = []
        self.gather_rows = []
        self.partials = {}
        self.forward = [[[] for _ in range(self.chunks)] for _ in range(SOURCES)]
        self.backward = [[[] for _ in range(self.chunks)] for _ in range(LOCAL_RANKS)]
        self.masks = array("i", [0]) * self.chunks

    def which_chunk(self, token):
        if token < self.first:
            return token * self.splits // self.first
        return token // self.chunk + self.splits - 1

    def config(self, mode):
        return (self.rank, SOURCES, self.tokens, self.hidden, TOPK,
                len(self.input_rows) or 1, len(self.gather_rows),
                self.chunk, self.splits, 0, self.reducers, mode, 0)

    def layout(self):
        partial_offset = self.gather_offset + align(len(self.gather_rows) * self.hidden * 2)
        workspace_bytes = partial_offset + align((len(self.input_rows) or 1) * self.hidden * 2)
        return (self.chunks, 1, self.controls, self.forward_capacity, self.backward_capacity, 0,
                self.gather_offset, partial_offset, workspace_bytes, LOCAL_RANKS * self.chunks * 2)

    def serialize(self):
        forward, counts, ranges = pack_tables(self.forward, self.forward_capacity, "i", 6)
        backward, _, backward_ranges = pack_tables(self.backward, self.backward_capacity, "q", 3)
        control = bytearray(self.gather_offset)
        for slot in range(3 * SOURCES, 3 * SOURCES + 64):
            struct.pack_into("<Q", control, slot * SLOT_BYTES, 1)
        golden_control = bytearray(control)
        for stage, mask in enumerate(self.masks):
            for peer in range(LOCAL_RANKS):
                if mask & (1 << peer):
                    slot = 3 * SOURCES + 96 + stage * LOCAL_RANKS + peer
                    struct.pack_into("<Q", golden_control, slot * SLOT_BYTES, 1)
        guard = struct.pack("<H", GUARD)
        layout = self.layout()
        init_gather = guard * ((layout[7] - layout[6]) // 2)
        golden_gather = b"".join(self.gather_rows)
        golden_gather += init_gather[len(golden_gather):]
        init_partial = guard * ((layout[8] - layout[7]) // 2)
        golden_partial = bytearray(init_partial)
        for row, payload in self.partials.items():
            offset = row * self.hidden * 2
            golden_partial[offset:offset + len(payload)] = payload
        input_data = b"".join(self.input_rows) or guard * self.hidden
        images = dict(input=input_data, workspace=bytes(control) + init_gather + init_partial,
                      forward=forward, forward_counts=counts, ranges=ranges, masks=self.masks.tobytes(),
                      backward=backward, backward_ranges=backward_ranges)
        golden_send = bytes(golden_control) + golden_gather + init_partial
        for reducer in range(self.reducers):
            slot = 3 * SOURCES + 96 + self.chunks * LOCAL_RANKS + reducer
            struct.pack_into("<I", golden_control, slot * SLOT_BYTES, self.chunks)
        golden_reduce = bytes(golden_control) + golden_gather + bytes(golden_partial)
        return images, golden_send, golden_reduce


def pack_tables(tables, capacity, code, width):
    table_bytes = array(code)
    counts, ranges = array("i"), array("i")
    for stages in tables:
        count = 0
        for records in stages:
            ranges.extend((count, count + len(records)))
            for record in records:
                table_bytes.extend(record)
            count += len(records)
        if count > capacity:
            raise ValueError("HT metadata exceeds its fixed capacity")
        table_bytes.extend(array(code, [0]) * ((capacity - count) * width))
        counts.append(count)
    return table_bytes.tobytes(), counts.tobytes(), ranges.tobytes()


def prepare(ranks, tokens, hidden, chunk, splits, reducers, seed):
    spec = [Rank(rank, tokens, hidden, chunk, splits, reducers) for rank in range(ranks)]
    routes = expert_routes(tokens, seed)
    for source, source_routes in enumerate(routes):
        for token, experts in enumerate(source_routes):
            servers = {}
            for slot, expert in enumerate(experts):
                rank = expert // EXPERTS_PER_RANK
                if rank >= ranks:
                    continue
                row = len(spec[rank].input_rows)
                values = contribution(source, token, slot, rank)
                payload = row_bytes(values, hidden)
                spec[rank].input_rows.append(payload)
                servers.setdefault(rank // LOCAL_RANKS, []).append((rank, row, values, payload))
            for group in servers.values():
                gateway, primary_row, primary_values, _ = group[0]
                receiver = spec[gateway]
                stage = receiver.which_chunk(token)
                records = receiver.forward[source][stage]
                end = sum(len(entries) for entries in receiver.forward[source]) + max(1, len(group) - 1)
                if len(group) == 1:
                    records.append((end, token, primary_row, -1, 0, -1))
                    continue
                total = primary_values.copy()
                for sender, local_row, values, payload in group[1:]:
                    gather_row = -1
                    if sender != gateway:
                        gather_row = len(receiver.gather_rows)
                        receiver.gather_rows.append(payload)
                        receiver.masks[stage] |= 1 << (sender % LOCAL_RANKS)
                        q = spec[sender].backward[gateway % LOCAL_RANKS][stage]
                        q.append((source, local_row, gather_row))
                    records.append((end, token, primary_row, sender, local_row, gather_row))
                    total = [a + b for a, b in zip(total, values)]
                receiver.partials[primary_row] = row_bytes(total, hidden)
    # Keep the registered workspace bounds and gather capacity equal across peers.
    # The candidate serializer validates remote gather indices using config.gather_rows.
    maximum = max(len(rank.gather_rows) for rank in spec)
    guard_row = struct.pack("<H", GUARD) * hidden
    for rank in spec:
        rank.gather_rows.extend([guard_row] * (maximum - len(rank.gather_rows)))
        for gateway in rank.backward:
            for stage, records in enumerate(gateway):
                if records:
                    source, local, gather = records[-1]
                    records[-1] = (source, local, gather | ((stage + 1) << 32))
    return spec, routes
