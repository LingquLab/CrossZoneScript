"""Construct the actual-input workspace and route tables for each independent HT return phase."""
import struct

RANKS = 64
LOCAL = 8
TOKENS = 4
ROW_BYTES = 512


def build_returns(config, real_partial, real_ready, inter_returned=None, inter_completed=None,
                  local_returned=None, local_done=None):
    """Retain reducer bytes and place completed return planes at their device ABI offsets."""
    rank = config["rank"]
    local_end = config["returnOffset"] - config["completionOffset"]
    returned_bytes = config["signalOffset"] - config["returnOffset"]
    assert config["reducerCount"] == 8 and config["chunks"] == TOKENS
    assert len(real_ready) == config["completionOffset"] - config["readyOffset"] == 8 * ROW_BYTES
    assert len(real_partial) == TOKENS * ROW_BYTES
    for offset in range(0, len(real_ready), ROW_BYTES):
        magic, epoch, reserved = struct.unpack_from("<qii", real_ready, offset)
        assert (magic, epoch, reserved) == (config["magic"], TOKENS, 0)
    workspace = bytearray(config["workspaceBytes"] + LOCAL * ROW_BYTES)
    workspace[config["partialOffset"]:config["partialOffset"] + len(real_partial)] = real_partial
    workspace[config["readyOffset"]:config["completionOffset"]] = real_ready

    # The two independent return kernels share routes. Even tokens use self,
    # odd tokens exercise local fullmesh. All tokens also have a remote source.
    other_local = rank // LOCAL * LOCAL + (rank + 1) % LOCAL
    peer_rows = {rank: [0, 2], other_local: [1, 3], rank ^ LOCAL: [0, 1, 2, 3]}
    capacity = config["forwardCapacity"]
    forward = bytearray(RANKS * capacity * 32)
    counts = [0] * RANKS
    ranges = [0] * (RANKS * TOKENS * 2)
    for peer, tokens in peer_rows.items():
        counts[peer] = len(tokens)
        for row, token in enumerate(tokens):
            struct.pack_into("<6i", forward, (peer * capacity + row) * 32, row + 1, token, token, 0, token, 0)
            slot = (peer * TOKENS + token) * 2
            ranges[slot:slot + 2] = row, row + 1

    is_final = any(data is not None for data in (inter_returned, inter_completed, local_returned, local_done))
    if is_final:
        assert len(inter_returned) == len(local_returned) == returned_bytes
        assert len(inter_completed) == local_end and len(local_done) == LOCAL * ROW_BYTES
        combined = bytearray(inter_returned)
        server = rank // LOCAL
        for token in range(TOKENS):
            offset = (token * LOCAL + server) * ROW_BYTES
            assert not any(combined[offset:offset + ROW_BYTES])
            combined[offset:offset + ROW_BYTES] = local_returned[offset:offset + ROW_BYTES]
        workspace[config["returnOffset"]:config["signalOffset"]] = combined
        workspace[config["completionOffset"]:config["returnOffset"]] = inter_completed
        workspace[config["workspaceBytes"]:] = local_done
    return (bytes(workspace), bytes(forward), struct.pack("<64i", *counts),
            struct.pack("<512i", *ranges))
