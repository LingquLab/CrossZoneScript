# Copyright (c) 2026 TileXR Project.
"""Decode per-lane evidence from the four fixed SIMT probe launches."""

import struct

WIDTHS = (2, 8, 32, 128)
LANES = 8
STRIPE = 512
TRIAL_BYTES = 12 * STRIPE
OUTPUT_BYTES = len(WIDTHS) * TRIAL_BYTES
FILL32 = 0xDEADBEEF
FILL64 = (FILL32 << 32) | FILL32
MARKER32 = 0x1A2B0000
MARKER64 = 0x5A70616101000000
GM_MARKER = 0x7B70616102000000


def decode(raw):
    if len(raw) != OUTPUT_BYTES:
        raise ValueError("wrong SIMT probe output length")
    trials = []
    for trial, width in enumerate(WIDTHS):
        base = trial * TRIAL_BYTES
        masks = dict(gm=0, packed_ub32=0, strided_ub64=0, scalar_readback64=0)
        touched = dict.fromkeys(masks, 0)
        for lane in range(LANES):
            bit = 1 << lane
            gm, = struct.unpack_from("<Q", raw, base + (4 + lane) * STRIPE)
            packed, = struct.unpack_from("<I", raw, base + lane * 4)
            if gm == GM_MARKER + lane:
                masks["gm"] |= bit
            if gm != 0:
                touched["gm"] |= bit
            if packed == MARKER32 + lane:
                masks["packed_ub32"] |= bit
            if packed != FILL32:
                touched["packed_ub32"] |= bit
            expected_row = tuple(MARKER64 + lane * 8 + i for i in range(8))
            for slot, name in enumerate(("strided_ub64", "scalar_readback64"), start=1):
                row = struct.unpack_from("<8Q", raw, base + slot * STRIPE + lane * 64)
                if row == expected_row:
                    masks[name] |= bit
                if row != (FILL64,) * 8:
                    touched[name] |= bit
        trials.append(dict(launch_width=width, expected_lane_mask=(1 << min(width, LANES)) - 1,
                           correct_lane_masks=masks, touched_lane_masks=touched))
    return trials
