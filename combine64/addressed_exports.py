#!/usr/bin/env python3
# Copyright (c) 2026 TileXR Project.
"""Decode addressed SST dumps into checker inputs using verified Die ownership.

Never fill missing exports with zeros or choose a Die from the observed values.
The raw dumps and their hashes remain available for later evidence checks.
"""

import argparse
import hashlib
import json
from pathlib import Path

# AddrAnalyzerV310 binary verified by the existing pinned SST output decoder.
V310_DECODER_SHA256 = "bf93fd6d0749fc3c6eaae365c7cba558988e1fa25c1b9a26e139be61b60a3da3"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_addressed(path, address, size):
    if address < 0 or size <= 0:
        raise ValueError("Need a nonempty chip-local output range")
    result, seen = bytearray(size), bytearray(size)
    with Path(path).open("rb") as stream:
        while True:
            record = stream.read(520)
            if not record:
                break
            if len(record) != 520:
                raise ValueError("Truncated addressed-512 dump: " + str(path))
            base = int.from_bytes(record[:8], "little")
            left, right = max(address, base), min(address + size, base + 512)
            if left >= right:
                continue
            first, last = left - address, right - address
            payload = record[8 + left - base : 8 + right - base]
            prior = seen[first:last]
            if any(prior):
                # Duplicate records must agree wherever their ranges overlap.
                for index, value in enumerate(payload, first):
                    if seen[index] and result[index] != value:
                        raise ValueError("Conflicting dump records: " + str(path))
            result[first:last] = payload
            seen[first:last] = b"\x01" * len(payload)
    if not all(seen):
        raise ValueError("Missing dump bytes: " + str(path))
    return bytes(result)


def reconstruct_die_output(paths, address, size, decoder_library):
    if len(paths) != 2 or size <= 0 or address < 0:
        raise ValueError("Need two Die dumps and a nonempty chip-local range")
    if digest(decoder_library) != V310_DECODER_SHA256:
        raise ValueError("Die ownership is unverified for this decoder library")
    copies = [read_addressed(path, address, size) for path in paths]
    result, offset = bytearray(size), 0
    while offset < size:
        current = address + offset
        die = owning_die(current)
        count = min(size - offset, 512 - current % 512)
        result[offset : offset + count] = copies[die][offset : offset + count]
        offset += count
    return bytes(result)


def owning_die(address):
    return ((address >> 9) ^ (address >> 16) ^ (address >> 23)) & 1


def read_die_stripe(paths, address, size, decoder_library):
    if len(paths) != 2 or address < 0 or not 0 < size <= 512 - address % 512:
        raise ValueError("Need a nonempty range within one stripe")
    if digest(decoder_library) != V310_DECODER_SHA256:
        raise ValueError("Die ownership is unverified for this decoder library")
    return read_addressed(paths[owning_die(address)], address, size)


def export_actual(layout, dump_dir, output, decoder_library):
    dump_dir, output = Path(dump_dir), Path(output)
    ranks = layout["ranks"]
    required = set(layout.get("required_exports", ("returned", "completed", "status", "output")))
    standard = {"returned", "completed", "status", "output"}
    if required - {"profiling", "local_profiling"} not in (standard, standard | {"gather", "partial", "barriers"}):
        raise ValueError("Unsupported checker export contract")
    if len(ranks) not in (16, 32, 64) or {row["rank"] for row in ranks} != set(range(len(ranks))):
        raise ValueError("Expected all logical ranks")
    # Resolve and validate every input before creating checker artifacts.
    decoded, hashes = [], {}
    for row in ranks:
        for name, target in row["exports"].items():
            if name not in required:
                raise ValueError("Unknown checker export: " + name)
            paths = [
                dump_dir / f"david_{row['rank']}_die{die}_{name}.bin" for die in (1, 2)
            ]
            hashes.update({str(p): digest(p) for p in paths})
            local = target["addr"] - row["sys_va_base_addr"]
            payload = reconstruct_die_output(
                paths, local, target["size"], decoder_library
            )
            decoded.append((row["rank"], name, payload))
        if set(row["exports"]) != required:
            raise ValueError(
                "Returned, completed and owner status exports are required"
            )
    if any(digest(p) != expected for p, expected in hashes.items()):
        raise ValueError("Raw dump changed during export")
    output.mkdir(parents=True, exist_ok=False)
    for rank, name, payload in decoded:
        folder = output / f"rank{rank:02d}"
        folder.mkdir(exist_ok=True)
        (folder / (name + ".bin")).write_bytes(payload)
    report = {
        "status": "exports_reconstructed_not_checked",
        "world": len(ranks),
        "decoder_sha256": V310_DECODER_SHA256,
        "raw_dump_sha256": hashes,
    }
    (output / "export-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def verify_guards(layout, dump_dir, decoder_library):
    """Check explicit boundary sentinels; absence is not a successful guard check."""
    checked = 0
    for row in layout["ranks"]:
        if not row.get("guards"):
            raise ValueError("Guard exports missing for rank " + str(row["rank"]))
        for guard in row["guards"]:
            paths = [
                Path(dump_dir) / f"david_{row['rank']}_die{die}_{guard['name']}.bin"
                for die in (1, 2)
            ]
            payload = reconstruct_die_output(
                paths,
                guard["addr"] - row["sys_va_base_addr"],
                guard["size"],
                decoder_library,
            )
            if payload != bytes([guard["expected_byte"]]) * guard["size"]:
                raise ValueError(
                    "Guard changed for rank " + str(row["rank"]) + ": " + guard["name"]
                )
            checked += 1
    return {"status": "guards_verified", "count": checked}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--dump-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decoder-library", type=Path, required=True)
    args = parser.parse_args()
    result = export_actual(
        json.loads(args.layout.read_text()),
        args.dump_dir,
        args.output,
        args.decoder_library,
    )
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "raw_dump_sha256"}, indent=2
        )
    )


if __name__ == "__main__":
    main()
