"""Generate deterministic SST inputs and compare raw vector lanes."""
import argparse
import json
from pathlib import Path
import struct

ENTRY = "tilexr_probe_unpack_b16"
WORDS = 64
INPUT = 0x100000000
OUTPUT = INPUT + 0x800
PREFIX_GUARD = 512
PAYLOAD = 3 * WORDS * 4
EXTENT = 1536  # Three complete SST dump records.
SUFFIX_GUARD = EXTENT - PREFIX_GUARD - PAYLOAD
MODES = ("bf16_norm", "bf16_unpack", "u16_unpack")


def input_words():
    return tuple(range(0x3F80, 0x3F80 + 2 * WORDS))


def generate(folder, mount):
    chip = folder / "chip0"
    chip.mkdir()
    (chip / "input.bin").write_bytes(struct.pack("<128H", *input_words()))
    (chip / "sentinels.bin").write_bytes(b"\xa5" * EXTENT)

    def pointer(value, offset, filename=None):
        field = {"value": value, "param_offset": str(offset), "param_type": "11"}
        if filename:
            field["input_bin"] = f"{mount}/chip0/{filename}"
        return field

    workload = {"workload_config": {"chip0": {
        "gm_size": 137438953472,
        "sys_va_base_addr": 0,
        "ub_base_addr": 524288,
        "stack_base_addr": 1048576,
        "param_base_addr": 2147483648,
        "block_dim": 1,
        "subblock_dim": 1,
        "kernel_file": f"{mount}/probe.o",
        "vec_func": ENTRY,
        "bin_param": {"addr": 1073741824, "size": 10485760},
        "kernel_param": {
            "input": pointer(INPUT, 0),
            "output": pointer(OUTPUT + PREFIX_GUARD, 8),
            "load_input": pointer(INPUT, 16, "input.bin"),
            "load_sentinels": pointer(OUTPUT, 24, "sentinels.bin"),
        },
        "output_param": {"raw": {"addr": OUTPUT, "size": EXTENT}},
    }}}
    (folder / "workload.yml").write_text(json.dumps(workload, indent=2) + "\n")


def compare(data):
    assert len(data) == EXTENT
    words = input_words()
    packed = tuple(words[i] | (words[i + 1] << 16) for i in range(0, len(words), 2))
    unpacked = words[:WORDS]
    checks = {}
    for i, mode in enumerate(MODES):
        actual = struct.unpack_from("<64I", data, PREFIX_GUARD + i * WORDS * 4)
        expected = packed if mode == "bf16_norm" else unpacked
        checks[mode] = {
            "wrong_lanes": sum(got != want for got, want in zip(actual, expected, strict=True)),
            "packed_lanes": sum(got == want for got, want in zip(actual, packed, strict=True)),
            "expected_first4": [f"{word:08x}" for word in expected[:4]],
            "observed_first4": [f"{word:08x}" for word in actual[:4]],
        }
    guards_ok = (data[:PREFIX_GUARD] == b"\xa5" * PREFIX_GUARD and
                 data[-SUFFIX_GUARD:] == b"\xa5" * SUFFIX_GUARD)
    return {"lanes": WORDS, "guards_ok": guards_ok, "checks": checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("generate", "check"))
    parser.add_argument("folder", type=Path)
    parser.add_argument("--mount", default="/work/unpack-b16")
    args = parser.parse_args()
    if args.action == "generate":
        generate(args.folder, args.mount)
        return
    from exports import reconstruct_die_output

    paths = [args.folder / "bin" / f"david_0_die{die}_raw.bin" for die in (1, 2)]
    data = reconstruct_die_output(paths, OUTPUT, EXTENT, Path("/opt/sim/plugins/libDavinciSimulator.so"))
    (args.folder / "actual-raw.bin").write_bytes(data)
    result = compare(data)
    print(json.dumps(result, separators=(",", ":")))
    if not result["guards_ok"] or any(row["wrong_lanes"] for row in result["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
