# Copyright (c) 2026 TileXR Project.
"""Serialize native registered UDMA resources for the existing 40-slot SST model.

Kernel tables are dense logical QP arrays; each logical queue owns a distinct physical
buffer/state. This models resources only and does not replace native queue APIs.
"""

import struct

GM_BYTES = 0x2000000000
WORLD, QPS, PHYSICAL_QPS, DEPTH, BB_BYTES = 64, 32, 40, 16384, 64
BASES = {
    "workspace": 0x200000000,
    "commArgs": 0x2FF000000,
    "udma_info": 0x300040000,
    "registry": 0x300060000,
    "sq_ctx": 0x310000000,
    "cq_ctx": 0x311000000,
    "mem_info": 0x312000000,
    "eid_table": 0x313000000,
    "native_sq_ctx": 0x314000000,
    "native_cq_ctx": 0x315000000,
    "native_mem_info": 0x316000000,
    "forward": 0x400000000,
    "counts": 0x401000000,
    "ranges": 0x402000000,
    "status": 0x403000000,
    "sq_buffers": 0x1900000000,
    "cq_buffers": 0x1A00000000,
    "queue_state": 0x1B00000000,
    "cq_doorbells": 0x1B01000000,
}
DOORBELL_BASE = 512 * GM_BYTES + 0x1C00000000
DIRECT_SLOTS = (8, 28, 9, 29, 10, 30, 11)


def physical_qp(qp, six=16, two=16):
    # The model reserves 20 physical slots on each uDie.
    if not (0 < six <= 20 and 0 < two <= 20 and 0 <= qp < six + two):
        raise ValueError("QP layout exceeds the two 20-slot SST uDies")
    return qp if qp < six else 20 + qp - six


def queue_addresses(rank, peer, qp, six=16, two=16):
    return physical_queue_addresses(rank, peer, physical_qp(qp, six, two))


def physical_queue_addresses(rank, peer, slot):
    if not 0 <= rank < WORLD or not 0 <= peer < WORLD or not 0 <= slot < PHYSICAL_QPS:
        raise ValueError("Invalid physical queue coordinates")
    flat = peer * PHYSICAL_QPS + slot
    base = rank * GM_BYTES
    states = WORLD * PHYSICAL_QPS * 64
    state = base + BASES["queue_state"] + flat * 64
    die, local = divmod(slot, 20)
    return {
        "flat": flat,
        "wqn": rank * WORLD * PHYSICAL_QPS + flat,
        "sq": base + BASES["sq_buffers"] + flat * DEPTH * BB_BYTES,
        "cq": base + BASES["cq_buffers"] + flat * DEPTH * BB_BYTES,
        "count": state,
        "sq_head": state + states,
        "sq_tail": state + 2 * states,
        "cq_head": state + 3 * states,
        "cq_tail": state + 4 * states,
        "cq_db": base + BASES["cq_doorbells"] + flat * 64,
        "db": DOORBELL_BASE + die * 0x100000000 + (peer * 20 + local) * BB_BYTES,
    }


def runtime_images(abi, rank, workspace_bytes, world=WORLD, direct=False):
    a = abi["runtime_abi"]
    config = {field["name"]: field["value"] for field in abi["fields"]}
    six, two = (config.get("config." + key, 0) or 16 for key in ("sixPortQps", "twoPortQps"))
    mapping = [physical_qp(qp, six, two) for qp in range(six + two)]
    if direct:
        assert (six, two) == (8, 8) and not set(mapping).intersection(DIRECT_SLOTS)
        mapping.extend(DIRECT_SLOTS)
    magic = config["config.magic"]
    sizes = {
        k: a[k]
        for k in ("info_bytes", "sq_bytes", "cq_bytes", "mem_bytes", "registry_bytes")
    }
    if sizes != {
        "info_bytes": 48,
        "sq_bytes": 80,
        "cq_bytes": 56,
        "mem_bytes": 40,
        "registry_bytes": 4112,
    }:
        raise ValueError("Native runtime ABI differs from the checked SST serializer")
    base = rank * GM_BYTES
    comm = bytearray(a["comm_bytes"])
    struct.pack_into("<iiiiI", comm, 0, rank, rank % 8, world, 8, 0x400)
    for peer in range(world):
        struct.pack_into(
            "<Q",
            comm,
            a["comm_peer_mems"] + peer * 8,
            peer * GM_BYTES + BASES["workspace"],
        )
        struct.pack_into(
            "<i", comm, a["comm_magics"] + peer * 4, magic
        )
    struct.pack_into("<Q", comm, a["comm_udma_info"], base + BASES["udma_info"])
    struct.pack_into("<Q", comm, a["comm_udma_registry"], base + BASES["registry"])
    struct.pack_into("<Q", comm, a["comm_generation"], 1)
    struct.pack_into("<Q", comm, a["comm_peer_bytes"], workspace_bytes)
    registry = bytearray(a["registry_bytes"])
    struct.pack_into("<IIII", registry, 0, 0x54585255, 1, world, 1)
    for peer in range(world):
        struct.pack_into(
            "<QQ",
            registry,
            a["registry_regions"] + peer * 16,
            peer * GM_BYTES + BASES["workspace"],
            workspace_bytes,
        )
    info = bytearray(a["info_bytes"])
    struct.pack_into("<I", info, 0, len(mapping))
    for key, image in (
        ("info_sq", "native_sq_ctx"),
        ("info_cq", "native_cq_ctx"),
        ("info_mem", "native_mem_info"),
    ):
        struct.pack_into("<Q", info, a[key], base + BASES[image])
    sq, cq, mem, eid = bytearray(), bytearray(), bytearray(), bytearray()
    for peer in range(world):
        for slot in range(PHYSICAL_QPS):
            q = physical_queue_addresses(rank, peer, slot)
            s, c, m = (
                bytearray(a["sq_bytes"]),
                bytearray(a["cq_bytes"]),
                bytearray(a["mem_bytes"]),
            )
            struct.pack_into("<I", s, 0, q["wqn"])
            struct.pack_into(
                "<QIIQQ", s, 8, q["sq"], 6, DEPTH, q["sq_head"], q["sq_tail"]
            )
            struct.pack_into(
                "<QIIQQ", s, 48, q["db"], q["wqn"] % 16, q["flat"], q["count"], 0
            )
            struct.pack_into("<I", c, 0, q["wqn"])
            struct.pack_into(
                "<QIIQQ", c, 8, q["cq"], 6, DEPTH, q["cq_head"], q["cq_tail"]
            )
            struct.pack_into("<Q", c, 48, q["cq_db"])
            m[0:2] = b"\x01\x01"
            registered = (
                (workspace_bytes + 2 * 1024 * 1024 - 1)
                // (2 * 1024 * 1024)
                * (2 * 1024 * 1024)
            )
            struct.pack_into(
                "<IIII",
                m,
                4,
                (rank << 10) | peer,
                (rank << 16) | q["flat"],
                0,
                registered,
            )
            struct.pack_into(
                "<QQ",
                m,
                24,
                peer * GM_BYTES + BASES["workspace"],
                base + BASES["eid_table"] + q["flat"] * 16,
            )
            eid.extend(struct.pack("<QQ", peer * GM_BYTES, 0))
            sq.extend(s)
            cq.extend(c)
            mem.extend(m)
    state_bytes = WORLD * PHYSICAL_QPS * 64
    native_tables = {}
    for name, table, width in (
        ("native_sq_ctx", sq, a["sq_bytes"]),
        ("native_cq_ctx", cq, a["cq_bytes"]),
        ("native_mem_info", mem, a["mem_bytes"]),
    ):
        compact = bytearray()
        for peer in range(world):
            for slot in mapping:
                start = (peer * PHYSICAL_QPS + slot) * width
                compact.extend(table[start : start + width])
        native_tables[name] = bytes(compact)
    return dict(
        commArgs=bytes(comm),
        registry=bytes(registry),
        udma_info=bytes(info),
        sq_ctx=bytes(sq),
        cq_ctx=bytes(cq),
        mem_info=bytes(mem),
        eid_table=bytes(eid),
        queue_state=bytes(5 * state_bytes),
        cq_doorbells=bytes(state_bytes),
        **native_tables,
    )
