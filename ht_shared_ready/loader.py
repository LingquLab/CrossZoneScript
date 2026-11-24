#!/usr/bin/env python3
# Copyright (c) 2026 TileXR Project.
"""Build SST input bindings from explicit ABI fields and initialized memory images.

The caller supplies the native kernel ABI, addresses and magic values. This module
does not compile kernels or infer SHMEM/TileXR runtime layouts.
"""

import hashlib
import json
import struct
from pathlib import Path, PurePosixPath

# Simulator loader slots, independent of Host typedef names.
TYPES = {
    "GM_ADDR": (11, "Q"),
    "uint64_t": (7, "Q"),
    "int64_t": (6, "q"),
    "uint32_t": (5, "I"),
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pack_arguments(fields, size):
    """Serialize resolved scalar fields, including flattened by-value structures."""
    packed, occupied, names = bytearray(size), bytearray(size), set()
    params = {}
    for field in fields:
        name, offset, value = field["name"], field["offset"], field["value"]
        if name in names or name.startswith("load_"):
            raise ValueError("Duplicate or reserved argument name: " + name)
        code, fmt = TYPES[field["type"]]
        width = struct.calcsize("<" + fmt)
        if (
            not isinstance(offset, int)
            or offset < 0
            or offset + width > size
            or any(occupied[offset : offset + width])
        ):
            raise ValueError("Overlapping or out-of-range ABI field: " + name)
        if offset % width:
            raise ValueError("Misaligned ABI field: " + name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("ABI value must be an integer: " + name)
        struct.pack_into("<" + fmt, packed, offset, value)
        occupied[offset : offset + width] = b"\x01" * width
        names.add(name)
        params[name] = {
            "value": value,
            "param_offset": str(offset),
            "param_type": str(code),
        }
    return bytes(packed), params


def initialize_region(size, writes):
    """Zero a region and apply disjoint writes, including caller-supplied magic words."""
    image, used = bytearray(size), bytearray(size)
    for offset, payload in writes:
        end = offset + len(payload)
        if offset < 0 or end > size or any(used[offset:end]):
            raise ValueError("Overlapping or out-of-range initialization")
        image[offset:end] = payload
        used[offset:end] = b"\x01" * len(payload)
    return bytes(image)


def prepare_loader(output, runtime_root, kernel, kernel_sha256, entry, ranks, *, blocks=16):
    """Write workload/input artifacts; runtime images are supplied by the caller.

    Each rank specifies abi_size, fields, gm_size, sys_va_base_addr, UB/stack/param
    addresses, binary address, images [{name, addr, data}], and export ranges.
    All ranks launch the same number of AIV blocks.
    """
    output, runtime_root, kernel = (
        Path(output),
        PurePosixPath(runtime_root),
        Path(kernel),
    )
    if not runtime_root.is_absolute() or ".." in runtime_root.parts:
        raise ValueError("runtime_root must be an absolute container path")
    if sha256(kernel) != kernel_sha256:
        raise ValueError("Blue-built kernel digest differs")
    if len(ranks) != 64 or {row["rank"] for row in ranks} != set(range(64)):
        raise ValueError("Expected all 64 logical ranks")
    if type(blocks) is not int or not 1 <= blocks <= 64:
        raise ValueError("Expected 1 to 64 AIV blocks")
    output.mkdir(parents=True, exist_ok=False)
    (output / "kernel").mkdir()
    binary_name = "inter_server_send.o"
    (output / "kernel" / binary_name).write_bytes(kernel.read_bytes())
    workload, regions = {}, []
    for row in sorted(ranks, key=lambda x: x["rank"]):
        rank = row["rank"]
        directory = output / "input_bin" / f"rank{rank:02d}"
        directory.mkdir(parents=True)
        args, params = pack_arguments(row["fields"], row["abi_size"])
        (directory / "kernel_args.bin").write_bytes(args)
        offset = (len(args) + 7) // 8 * 8
        intervals, image_names = [], set()
        low, high = row["sys_va_base_addr"], row["sys_va_base_addr"] + row["gm_size"]
        for image in row["images"]:
            name, address, data = image["name"], image["addr"], image["data"]
            if (
                not name
                or Path(name).name != name
                or "/" in name
                or "\\" in name
                or name in image_names
            ):
                raise ValueError("Invalid or duplicate image name")
            end = address + len(data)
            if not low <= address < end <= high or any(
                address < b and a < end for a, b in intervals
            ):
                raise ValueError("Overlapping or out-of-GM input region")
            intervals.append((address, end))
            image_names.add(name)
            filename = name + ".bin"
            (directory / filename).write_bytes(data)
            params["load_" + name] = {
                "value": address,
                "param_offset": str(offset),
                "param_type": "11",
                "input_bin": str(
                    runtime_root / "input_bin" / directory.name / filename
                ),
            }
            offset += 8
        exports = row["exports"]
        output_params = dict(exports)
        for guard in row.get("guards", []):
            name = guard["name"]
            if not name or "/" in name or "\\" in name or name in output_params:
                raise ValueError("Invalid or duplicate guard export")
            if not 0 <= guard["expected_byte"] <= 255:
                raise ValueError("Invalid guard sentinel byte")
            output_params[name] = {"addr": guard["addr"], "size": guard["size"]}
        for value in output_params.values():
            if (
                value["size"] <= 0
                or not low <= value["addr"] < value["addr"] + value["size"] <= high
            ):
                raise ValueError("Export outside rank GM")
        workload[f"chip{rank}"] = {
            "gm_size": row["gm_size"],
            "sys_va_base_addr": low,
            "ub_base_addr": row["ub_base_addr"],
            "stack_base_addr": row["stack_base_addr"],
            "param_base_addr": row["param_base_addr"],
            "block_dim": blocks,
            "subblock_dim": 1,
            "kernel_file": str(runtime_root / "kernel" / binary_name),
            "vec_func": entry,
            "kernel_param": params,
            "bin_param": {
                "addr": row["binary_addr"],
                "size": max(
                    0x26C40, (kernel.stat().st_size + 511) // 512 * 512, offset
                ),
            },
            "output_param": output_params,
        }
        regions.append(
            {
                "rank": rank,
                "sys_va_base_addr": low,
                "exports": exports,
                "guards": row.get("guards", []),
            }
        )
    # JSON is accepted by the SST YAML loader and needs no blue PyYAML dependency.
    (output / "workload.yml").write_text(
        json.dumps({"workload_config": workload}, indent=2) + "\n"
    )
    (output / "export-layout.json").write_text(
        json.dumps({"ranks": regions}, indent=2) + "\n"
    )
    files = {
        str(p.relative_to(output)): sha256(p)
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    manifest = {
        "status": "loader_prepared_not_simulated",
        "entry": entry,
        "block_dim": blocks,
        "world": 64,
        "kernel_sha256": kernel_sha256,
        "files": files,
    }
    (output / "loader-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
