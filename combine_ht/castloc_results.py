# Copyright (c) 2026 TileXR Project.
"""Pack fixed FP32 and BF16 controls and compare UB captures with direct GM writes."""

import struct

ELEMENTS = 256
OUTPUT_BYTES = 4096
BF16_BYTES = ELEMENTS * 2
FP32_BYTES = ELEMENTS * 4


def inputs(bf16_bits):
    values = [2 + (i % 32) / 16 for i in range(ELEMENTS // 2)]
    values += [2 + (2 * i + 1) / 128 for i in range(ELEMENTS // 2)]
    fp32 = struct.pack("<256f", *values)
    reference = tuple(bf16_bits(value) for value in values)
    return fp32 + struct.pack("<256H", *reference), fp32, reference


def decode(raw, fp32, reference):
    if len(raw) != OUTPUT_BYTES:
        raise ValueError("incorrect CAST_RINT probe output size")
    bf16 = [struct.unpack_from("<256H", raw, section * BF16_BYTES) for section in range(2)]
    captures = [struct.unpack_from("<256I", raw, 2 * BF16_BYTES + section * FP32_BYTES)
                for section in range(2)]
    bits = [[word & 0xFFFF for word in capture] for capture in captures]
    tag_errors = [sum((word >> 16) != 0xA500 + i for i, word in enumerate(capture)) for capture in captures]
    fp32_mismatches = sum(a != b for a, b in zip(raw[-FP32_BYTES:], fp32))
    control_ub_errors = sum(actual != expected for actual, expected in zip(bits[0], reference))
    control_gm_errors = sum(actual != expected for actual, expected in zip(bf16[0], reference))
    cast_ub_errors = sum(actual != expected for actual, expected in zip(bits[1], reference))
    cast_gm_errors = sum(actual != expected for actual, expected in zip(bf16[1], reference))
    ub_to_gm_errors = sum(ub != gm for ub, gm in zip(bits[1], bf16[1]))
    first = next((i for i, (ub, gm, expected) in enumerate(zip(bits[1], bf16[1], reference))
                  if ub != expected or gm != expected), None)
    return dict(control_ub_wrong_elements=control_ub_errors,
                control_gm_wrong_elements=control_gm_errors,
                fp32_echo_wrong_bytes=fp32_mismatches,
                control_tag_errors=tag_errors[0], cast_tag_errors=tag_errors[1],
                cast_ub_wrong_elements=cast_ub_errors, cast_gm_wrong_elements=cast_gm_errors,
                cast_ub_mismatches_gm=ub_to_gm_errors,
                first_mismatch=(None if first is None else
                                dict(index=first, expected=hex(reference[first]),
                                     ub=hex(bits[1][first]), gm=hex(bf16[1][first]))))
