"""Audit both reduction references from the retained workload and actual dumps."""
from collections import Counter
import json
import math
from pathlib import Path
import struct

case = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/ht-tunable-workspace-186dfa18-bs4-bf16-castfix")
workload = json.loads((case / "workload.yml").read_bytes())["workload_config"]


def words(path, word):
    return [entry[0] for entry in struct.iter_unpack("<" + word, path.read_bytes())]


def value(word):
    return struct.unpack("<f", struct.pack("<I", word << 16))[0]


def rounded(number):
    fraction, exponent = math.frexp(number)
    return math.ldexp(round(fraction * 256), exponent - 8)


def sum_fp32(values):
    total = 0.0
    for item in values:
        total = struct.unpack("<f", struct.pack("<f", total + item))[0]
    return total


errors = Counter()
tested = Counter()
samples = {}
for rank in range(64):
    name = f"rank{rank:02d}"
    inputs = case / "input_bin" / name
    actual = case / "actual" / name
    golden = case / "fixture-data" / name
    params = workload[f"chip{rank}"]["kernel_param"]
    config = {key.removeprefix("send.config."): row["value"] for key, row in params.items()
              if key.startswith("send.config.")}
    hidden, tokens = config["hidden"], config["tokens"]
    stride = max(config["topk"], config["world"] // 8)
    assert (hidden, tokens) == (256, 4)
    input_offset = params["local.input"]["value"] - params["load_workspace"]["value"]
    workspace = (inputs / "workspace.bin").read_bytes()
    native_rows = [value(word) for word, in struct.iter_unpack("<H", workspace[
        input_offset:input_offset + config["inputRows"] * hidden * 2])]
    gather = [value(word) for word in words(actual / "gather.bin", "H")]
    partial = [value(word) for word in words(actual / "partial.bin", "H")]
    golden_partial = [value(word) for word in words(golden / "golden-partial.bin", "H")]
    routes = list(struct.iter_unpack("<6i", (inputs / "forward.bin").read_bytes()))
    primary_rows = set()
    for peer, count in enumerate(words(inputs / "counts.bin", "i")):
        base = peer * config["forwardCapacity"]
        cursor = 0
        while cursor < count:
            end, token, primary, _, _, _ = routes[base + cursor]
            assert cursor < end <= count and primary not in primary_rows
            primary_rows.add(primary)
            sources = [(native_rows, primary)]
            for _, _, _, contributor, local_row, gather_row in routes[base+cursor:base+end]:
                sources.append((native_rows, local_row) if contributor == rank else (gather, gather_row))
            for column in range(hidden):
                provided = [buffer[row * hidden + column] for buffer, row in sources]
                reference = rounded(sum_fp32(provided))
                index = primary * hidden + column
                tested["partial"] += 1
                errors["partial_vs_golden"] += reference != golden_partial[index]
                errors["partial_vs_sst"] += reference != partial[index]
                if (rank, primary, column) == (0, 0, 192):
                    samples["partial"] = {"input": provided, "cpu": reference, "golden": golden_partial[index],
                                          "sst": partial[index], "token": token, "column": column}
            cursor = end
    assert primary_rows == set(range(config["partialRows"]))
    assert params["serverMask"]["value"] == params["load_server_mask"]["value"]
    returned = [value(word) for word in words(actual / "returned.bin", "H")]
    output = [value(word) for word in words(actual / "output.bin", "H")]
    for token, mask in enumerate(words(inputs / "server_mask.bin", "Q")):
        servers = [server for server in range(stride) if mask & (1 << server)]
        assert servers == sorted((rank // 8, (rank // 8) ^ 1))
        for column in range(hidden):
            provided = [returned[(token * stride + server) * hidden + column] for server in servers]
            reference = rounded(sum_fp32(provided))
            index = token * hidden + column
            tested["final"] += 1
            errors["final_vs_sst"] += reference != output[index]
            if (rank, token, column) == (0, 0, 4):
                samples["final"] = {"input": provided, "cpu": reference, "sst": output[index],
                                    "token": token, "column": column}
print(json.dumps(dict(tested=tested, mismatches=errors, samples=samples), separators=(",", ":")))
