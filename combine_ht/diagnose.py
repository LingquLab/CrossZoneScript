# Copyright (c) 2026 TileXR Project.
"""Read the retained 8P smoke outputs and summarize mismatch patterns on each Die."""

from collections import Counter
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import sys

WORK = Path("/work")
SEND = "8p-bs4-20261120T071927854826/send-only"
REDUCE = "8p-bs4-20261120T072359953771/reduce-only"


def local_report():
    sys.path.insert(0, str(WORK))
    import sst
    import yaml

    specs, _ = sst.fixtures.prepare(8, 4, 256, 16, 4, 40, 64)
    send, reduce = {}, {}
    for mode, relative, result in ((1, SEND, send), (2, REDUCE, reduce)):
        case = WORK / "cases" / relative
        chips = list(yaml.safe_load((case / "workload.yml").read_text())["workload_config"].values())
        misses, examples, by_rank = Counter(), [], []
        for spec, chip in zip(specs, chips):
            rank = spec.rank
            out = chip["output_param"]["workspace"]
            address = out["addr"] - chip["sys_va_base_addr"]
            paths = [case / "bin" / f"david_{rank}_die{die}_workspace.bin" for die in (1, 2)]
            _, golden_send, golden_reduce = spec.serialize()
            layout = spec.layout()
            first, last = (layout[6], layout[7]) if mode == 1 else (layout[7], layout[8])
            views = [sst.extract_ranges([path], {"rows": (address + first, last - first)})["rows"]
                     for path in paths]
            golden = golden_send if mode == 1 else golden_reduce
            origins = {}
            if mode == 1:
                for src in specs:
                    for chunk, rows in enumerate(src.backward[rank]):
                        for pos, (source, input_row, packed) in enumerate(rows):
                            origins[packed & 0xffffffff] = (src.rank, source, input_row, chunk, pos, len(rows))
            else:
                for source, chunks in enumerate(spec.forward):
                    for chunk, records in enumerate(chunks):
                        for _, token, row, contributor, local, gather in records:
                            if contributor < 0:
                                continue
                            operands = origins.setdefault(row, [source, token, chunk, []])[3]
                            operands.append((contributor, local, gather))
            rank_bad = 0
            for row in range((last - first) // 512):
                start = row * 512
                local_address = address + first + start
                die = ((local_address >> 9) ^ (local_address >> 16) ^ (local_address >> 23)) & 1
                expected = golden[first + start:first + start + 512]
                actual = views[die][start:start + 512]
                if actual == expected:
                    continue
                alt = views[1 - die][start:start + 512]
                rank_bad += 1
                origin = origins.get(row)
                misses["wrong_die_only" if alt == expected else "neither_die_matches"] += 1
                misses["all_guard_on_expected_die"] += int(actual == b"\x88\xc1" * 256)
                misses["output_at_guard_row"] += int(origin is None)
                if mode == 1 and origin:
                    _, _, _, _, position, batch_len = origin
                    misses["first_in_batch"] += int(position == 0)
                    misses["last_in_batch"] += int(position + 1 == batch_len)
                    misses["middle_in_batch"] += int(0 < position < batch_len - 1)
                if len(examples) < 6:
                    example = dict(rank=rank, row=row, die=die + 1, origin=origin,
                                   expected_bf16=struct.unpack_from("<8H", expected),
                                   actual_bf16=struct.unpack_from("<8H", actual),
                                   other_bf16=struct.unpack_from("<8H", alt))
                    if mode == 2 and origin:
                        operand_rows = [spec.input_rows[row]]
                        for contributor, local, gather in origin[3]:
                            operand_rows.append(spec.input_rows[local] if contributor == rank else spec.gather_rows[gather])
                        example["operand_bf16_lane0"] = [struct.unpack_from("<H", value)[0] for value in operand_rows]
                    examples.append(example)
            by_rank.append(rank_bad)
        result.update(case=relative, counts=dict(misses), mismatches_by_rank=by_rank, first_examples=examples)
    print(json.dumps(dict(send=send, reduce=reduce), ensure_ascii=True))


def main():
    if sys.platform == "linux":
        local_report()
        return 0
    key = Path(os.environ["USERPROFILE"]) / ".ssh" / "id_ed25519"
    command = shlex.join(["timeout", "45", "docker", "exec", "-i",
                          "tilexr-combine-ht-c1d52c19", "python3", "-"])
    result = subprocess.run(["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes",
                             "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                             "-o", "ConnectTimeout=12", "root@141.61.94.26", command],
                            input=Path(__file__).read_bytes(), capture_output=True, timeout=58)
    if result.returncode:
        return result.returncode
    print(json.dumps(json.loads(result.stdout), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
