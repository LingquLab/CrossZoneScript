# Copyright (c) 2026 TileXR Project.
"""Load SST profiling buffers and write records for the TileXR Perfetto tool."""

import hashlib
import json
import struct
import uuid
from pathlib import Path

from exports import V310_DECODER_SHA256, read_addressed
from tilexr_profiling.model import RECORDS_MAGIC
from tilexr_profiling.records import FILE_HEADER, RECORD, STREAM_HEADER

OWNERS = 16
CAPACITY = 512
STATE_STRIDE = 16
PROFILE_OFFSET = 0x1000
STATE_BYTES = OWNERS * STATE_STRIDE * 4
TIMESTAMPS = 512
EVENTS = TIMESTAMPS + OWNERS * CAPACITY * 8
COUNTS = EVENTS + OWNERS * CAPACITY * 4
DROPPED = COUNTS + STATE_BYTES
STATES = DROPPED + STATE_BYTES
SIZE = STATES + STATE_BYTES

STAGES = (
    ("kernel_total", "total", 1, 2),
    ("init", "setup", 10, 11),
    ("ready_check", "wait", 20, 21),
    ("route_read", "copy", 30, 31),
    ("wqe_post", "issue", 40, 41),
    ("sq_doorbell", "issue", 50, 51),
    ("so_submit", "issue", 60, 61),
    ("drain", "finalize", 70, 71),
    ("cq_wait", "wait", 80, 81),
)


def initial_image(base):
    image = bytearray(SIZE)
    struct.pack_into(
        "<8I7Q",
        image,
        0,
        2,
        88,
        1,
        OWNERS,
        CAPACITY,
        CAPACITY,
        STATE_STRIDE,
        0,
        *(base + offset for offset in (TIMESTAMPS, EVENTS, COUNTS, DROPPED, STATES)),
        0,
        1,
    )
    return image


def configure_workload(case, workload, runtime_root):
    for name, chip in workload["workload_config"].items():
        rank = int(name.removeprefix("chip"))
        params = chip["kernel_param"]
        target = chip["output_param"]["status"]["addr"] + PROFILE_OFFSET
        relative = f"input_bin/rank{rank:02d}/profile.bin"
        (case / relative).write_bytes(initial_image(target))
        offset = max(int(p["param_offset"]) for p in params.values()) + 8
        params["load_profile"] = {
            "value": target,
            "param_type": "11",
            "param_offset": str(offset),
            "input_bin": f"{runtime_root}/{relative}",
        }
        chip["output_param"]["profile"] = {"addr": target, "size": SIZE}


def pack_stream(rank, owner, data):
    slot = owner * STATE_STRIDE * 4
    count, lost, state = (
        struct.unpack_from("<I", data, start + slot)[0]
        for start in (COUNTS, DROPPED, STATES)
    )
    if not 2 <= count <= CAPACITY or lost or state != 2:
        raise ValueError(
            f"Incomplete profile stream rank={rank} owner={owner}: {count=}, {lost=}, {state=}"
        )
    pairs = []
    for index in range(owner * CAPACITY, owner * CAPACITY + count):
        timestamp = struct.unpack_from("<Q", data, TIMESTAMPS + index * 8)[0]
        event = struct.unpack_from("<I", data, EVENTS + index * 4)[0]
        pairs.append((timestamp, event))
    if pairs[0][1] != 1 or pairs[-1][1] != 2:
        raise ValueError(f"Missing kernel endpoints for rank={rank} owner={owner}")
    return STREAM_HEADER.pack(
        (rank << 16) | owner, count, 0, 1, STREAM_HEADER.size + count * RECORD.size
    ) + b"".join(RECORD.pack(timestamp, event, 0) for timestamp, event in pairs)


def export_records(case, cycles_per_us):
    workload = json.loads((case / "workload.yml").read_text())["workload_config"]
    env = json.loads((case / "sst-environment.json").read_text())
    digest = hashlib.sha256(Path(env["decoder_library"]).read_bytes()).hexdigest()
    if digest != V310_DECODER_SHA256:
        raise ValueError("The SST decoder identity differs from the known Die mapping")
    streams = []
    for rank in range(64):
        chip = workload[f"chip{rank}"]
        address = chip["output_param"]["profile"]["addr"] - chip["sys_va_base_addr"]
        dies = [
            read_addressed(
                case / "bin" / f"david_{rank}_die{die}_profile.bin", address, SIZE
            )
            for die in (1, 2)
        ]
        data = bytearray()
        for offset in range(0, SIZE, 512):
            current = address + offset
            die = ((current >> 9) ^ (current >> 16) ^ (current >> 23)) & 1
            data.extend(dies[die][offset : offset + 512])
        streams.extend(pack_stream(rank, owner, data) for owner in range(OWNERS))
    capture_id = uuid.uuid4()
    payload = b"".join(streams)
    header = FILE_HEADER.pack(
        RECORDS_MAGIC,
        1,
        0,
        FILE_HEADER.size,
        RECORD.size,
        STREAM_HEADER.size,
        1,
        0,
        len(streams),
        capture_id.bytes,
        FILE_HEADER.size + len(payload),
    )
    mapping = {
        "schema": "tilexr_profiling_mapping.v1",
        "operator": "InterServerSend",
        "cycles_per_us": cycles_per_us,
        "stages": [
            dict(
                name=name,
                category=category,
                begin=begin,
                end=end,
                **(
                    {"critical": True}
                    if begin == 1
                    else {"parent": "drain" if begin == 80 else "kernel_total"}
                ),
            )
            for name, category, begin, end in STAGES
        ],
    }
    (case / "profile.records.bin").write_bytes(header + payload)
    (case / "profile.mapping.json").write_text(json.dumps(mapping, indent=2) + "\n")
    return len(streams)


if __name__ == "__main__":
    import sys

    print(export_records(Path(sys.argv[1]), float(sys.argv[2])))
