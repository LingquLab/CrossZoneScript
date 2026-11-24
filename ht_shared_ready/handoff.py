"""Rebind the saved local Gather fixture to a shared 8-slot HT reduction image."""
import struct

SLOTS = 8
TOKENS = 4
ROW_BYTES = 512
READY_BYTES = SLOTS * ROW_BYTES
WORLD = 8
MASK_RANK = 0xFF
GATHER_START = 3 * WORLD + 96
FIELDS = 6


def build_attached(args, forward, input_rows, gather_dump, gather_flags, config):
    """Return new 288 B args, unified registered workspace, ranges and masks."""
    local = bytearray(args)
    assert len(local) == 248
    rank, world, tokens, hidden, _ = struct.unpack_from("<5I", local, 112)
    assert (world, tokens, hidden) == (WORLD, TOKENS, 128)
    assert len(gather_dump) == 32 * ROW_BYTES and len(input_rows) == TOKENS * ROW_BYTES
    assert len(gather_flags) >= READY_BYTES
    assert config["chunks"] == TOKENS and config["reducerCount"] == SLOTS
    assert config["partialOffset"] == TOKENS * ROW_BYTES
    workspace, input_pointer = struct.unpack_from("<2Q", local, 0)
    _, _, _, forward_cap, backward_cap, _ = struct.unpack_from("<6I", local, 168)
    assert len(forward) == WORLD * forward_cap * FIELDS * 4
    controls = GATHER_START + TOKENS * SLOTS + 64 + WORLD * TOKENS * 2 + 64
    gather = controls * ROW_BYTES
    partial = gather + len(gather_dump)
    local_offset = config["workspaceBytes"] + READY_BYTES
    image = bytearray(local_offset + partial)
    image[config["inputOffset"]:config["inputOffset"] + len(input_rows)] = input_rows
    image[local_offset + gather:local_offset + partial] = gather_dump
    for token in range(TOKENS):
        off = local_offset + (GATHER_START + token * SLOTS) * ROW_BYTES
        image[off:off + READY_BYTES] = gather_flags[:READY_BYTES]
    ranges = bytearray(WORLD * TOKENS * 8)
    run_start = 0
    while run_start < forward_cap:
        offset = (rank * forward_cap + run_start) * FIELDS * 4
        run_end, token = struct.unpack_from("<2i", forward, offset)
        if not run_end:
            break
        assert 0 <= token < TOKENS and run_start < run_end <= forward_cap
        struct.pack_into("<2i", ranges, (rank * TOKENS + token) * 8, run_start, run_end)
        run_start = run_end
    assert run_start == TOKENS * (SLOTS - 1)
    struct.pack_into("<2Q", local, 0, workspace + local_offset, workspace)
    struct.pack_into("<I", local, 144, TOKENS)     # first_chunk_splits
    struct.pack_into("<I", local, 156, 2)          # REDUCE_ONLY
    struct.pack_into("<6I4Q", local, 168, TOKENS, 1, controls, forward_cap, backward_cap,
                     0, gather, partial, partial, WORLD * TOKENS * 2)
    local.extend(struct.pack("<3Qq2I", local_offset, workspace + config["partialOffset"],
                             workspace + config["readyOffset"], config["magic"], TOKENS, 0))
    assert len(local) == 288 and input_pointer != workspace
    exports = {
        "output": (workspace + config["partialOffset"], TOKENS * ROW_BYTES),
        "completed": (workspace + config["readyOffset"], READY_BYTES),
        "returned": (workspace + local_offset + gather, len(gather_dump)),
    }
    return bytes(local), bytes(image), bytes(ranges), struct.pack("<4I", *([MASK_RANK ^ (1 << rank)] * TOKENS)), exports
