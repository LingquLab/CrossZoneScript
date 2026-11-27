"""Prepare and check an isolated EP64 final reduction with eight input rows per token."""
import array
import hashlib
import json
from pathlib import Path
import struct
import sys

HIDDEN = 128
BLOCKS = 16
WORLD = 64
SERVERS = 8
SLOT = 512
MAGIC = 2026
WORKSPACE = 0x100000000
MASKS = 0x110000000
READY = 0x120000000
OUTPUT = 0x130000000
STATUS = 0x140000000
DECODER = "/opt/sim/plugins/libDavinciSimulator.so"


def generate(folder, mount, spec, tokens, mode):
    def field(value, offset, kind=11, filename=None):
        result = {"value": value, "param_offset": str(offset), "param_type": str(kind)}
        if filename:
            result["input_bin"] = f"{mount}/{filename}"
        return result

    fields = {name: field(value, i * 8) for i, (name, value) in enumerate(
        zip(("workspace", "masks", "ready", "output", "status"), (WORKSPACE, MASKS, READY, OUTPUT + SLOT, STATUS)))}
    values = {row["name"].removeprefix("config."): row["value"] for row in spec}
    assert (values["tokens"], values["hidden"], values["world"], values["topk"]) == (tokens, HIDDEN, WORLD, SERVERS)
    overrides = {"syncMode": {"full": 0, "window": 0, "physical": 2}[mode], "reserved": int(mode == "full")}
    types = {"GM_ADDR": 11, "uint64_t": 7, "int64_t": 6, "uint32_t": 5}
    for row in spec:
        name = row["name"].removeprefix("config.")
        fields[row["name"]] = field(overrides.get(name, row["value"]), row["offset"] - 8, types[row["type"]])
    assert max(int(row["param_offset"]) for row in fields.values()) == 180
    chunks = values["chunks"]
    workspace = bytearray(values["workspaceBytes"])
    for offset in range(values["completionOffset"], values["returnOffset"], SLOT):
        struct.pack_into("<q", workspace, offset, MAGIC)

    rows, expected = array.array("f"), array.array("f")
    assert sys.byteorder == "little"
    for token in range(tokens):
        accum = [0] * HIDDEN
        for server in range(SERVERS):
            row = [((token * 13 + server * 17 + column * 7) % 97) - 48 for column in range(HIDDEN)]
            rows.extend(value / 16 for value in row)
            for column, value in enumerate(row):
                accum[column] += value
        expected.extend(value / 16 for value in accum)
    returned = rows.tobytes()
    begin = values["returnOffset"]
    assert begin + len(returned) == values["signalOffset"]
    workspace[begin:begin + len(returned)] = returned
    (folder / "golden.bin").write_bytes(expected.tobytes())

    local_ready = bytearray(SERVERS * SLOT)
    for gateway in range(SERVERS):
        for step in range(chunks):
            struct.pack_into("<q", local_ready, gateway * SLOT + step * 8, MAGIC)
    seeds = {
        "workspace": (WORKSPACE, workspace),
        "masks": (MASKS, struct.pack("<Q", 0xff) * tokens),
        "ready": (READY, local_ready),
        "output": (OUTPUT, b"\xa5" * (2 * SLOT + len(expected) * 4)),
        "status": (STATUS, b"\xff" * (BLOCKS * 32)),
    }
    for i, (name, (address, data)) in enumerate(seeds.items()):
        filename = name + ".bin"
        (folder / filename).write_bytes(data)
        fields["load_" + name] = field(address, 184 + i * 8, filename=filename)
    exports = {name: {"addr": seeds[name][0], "size": len(seeds[name][1])} for name in ("output", "status")}
    chip = dict(gm_size=137438953472, sys_va_base_addr=0, ub_base_addr=524288,
                stack_base_addr=1048576, param_base_addr=2147483648, block_dim=BLOCKS, subblock_dim=1,
                kernel_file=f"{mount}/probe.o", vec_func="tilexr_probe_final_reduce_bench",
                bin_param={"addr": 1073741824, "size": 10485760}, kernel_param=fields, output_param=exports)
    (folder / "v310_config/workload_config.yml").write_text(json.dumps({"workload_config": {"chip0": chip}}))
    steps = {"full": 0, "window": min(3, (tokens + 255) // 256), "physical": chunks}[mode]
    return {"tokens": tokens, "steps": steps, "mode": mode, "partial_sha256": hashlib.sha256(returned).hexdigest()}


def check(folder):
    from exports import reconstruct_die_output

    workload = json.loads((folder / "v310_config/workload_config.yml").read_text())
    data = {}
    for name, spec in workload["workload_config"]["chip0"]["output_param"].items():
        paths = [folder / "bin" / f"david_0_die{die}_{name}.bin" for die in (1, 2)]
        data[name] = reconstruct_die_output(paths, spec["addr"], spec["size"], Path(DECODER))
    payload = data["output"][SLOT:-SLOT]
    expected = (folder / "golden.bin").read_bytes()
    status = [struct.unpack_from("<I", data["status"], i * 32)[0] for i in range(BLOCKS)]
    assert len(payload) == len(expected)
    mismatch = next((i for i in range(0, len(payload), 4) if payload[i:i + 4] != expected[i:i + 4]), None)
    return {"output_fp32": len(payload) // 4, "first_mismatch": mismatch, "status_zero": status.count(0),
            "guards_ok": data["output"][:SLOT] == data["output"][-SLOT:] == b"\xa5" * SLOT,
            "output_sha256": hashlib.sha256(payload).hexdigest()}


if __name__ == "__main__":
    print(json.dumps(check(Path(sys.argv[1])), separators=(",", ":")))
