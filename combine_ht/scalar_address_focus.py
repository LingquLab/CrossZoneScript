# Copyright (c) 2026 TileXR Project.
"""Correlate invalid read addresses with queue pointers and inspect ld_dev.

Reads baseline queue images and installed compiler headers, then emits assembly
for the pinned source. No simulator events or production source edits occur.
"""

import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
from tempfile import mkdtemp

import sst


def main():
    case = Path(json.loads((sst.WORK / "progress.json").read_text())["case"])
    assert case.parent == sst.WORK / "cases"
    baseline = case / "send-only"
    snapshot = json.loads((baseline / "environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    report = Path(mkdtemp(prefix="scalar-address-", dir=case))
    raw = (baseline / "input_bin/rank4/sq_ctx.bin").read_bytes()
    matches = []
    for bad in (0x4d800005200, 0x4d800028000):
        for entry in range(8 * 40):
            for field, offset in (("bufAddr",8),("headAddr",24),("tailAddr",32),("dbAddr",48),("wqeCntAddr",64)):
                pointer = struct.unpack_from("<Q", raw, entry * 80 + offset)[0]
                if pointer * 8 == bad:
                    matches.append(dict(bad_address=hex(bad), field=field, peer=entry//40, qp=entry%40, pointer=hex(pointer), factor=8))
    env = snapshot["settings"]
    cann = Path(env["cann_root"])
    roots = [cann / "compiler/tikcpp", cann / "aarch64-linux/ascendc/include", cann / "tools/bisheng_compiler/lib/clang", cann / "aarch64-linux/pkg_inc"]
    roots = [str(path) for path in roots if path.is_dir()]
    rg = shutil.which("rg")
    search = ([rg, "-n", "-m", "2", r"ld_dev\s*\(|define\s+ld_dev", *roots] if rg else
              ["grep", "-R", "-n", "-m", "2", "ld_dev", *roots])
    found = subprocess.run(search, capture_output=True, text=True, timeout=45)
    (report / "ld-dev-references.txt").write_text(found.stdout)
    selected = [line[:600] for line in found.stdout.splitlines() if re.search(r"(?:inline|define|extern|__attribute__|uint64_t|int64_t|__aicore__).{0,100}ld_dev|ld_dev.{0,100}(?:GM_ADDR|uint64_t|uint32_t|input|addr|offset)", line)]
    compile_command = json.loads((sst.WORK / "build.json").read_text())[0]
    assembly = report / "combine_ht.s"
    compile_command[compile_command.index("-c")] = "-S"
    compile_command[compile_command.index("-o")+1] = str(assembly)
    with (report / "compile.log").open("wb") as log:
        build = subprocess.run(sst.shell_command(env, "cann", compile_command), stdout=log, stderr=subprocess.STDOUT, timeout=90)
    result = dict(report=str(report), pointer_matches=matches, ld_dev_references=selected[:6], assembly_returncode=build.returncode)
    if not build.returncode:
        lines = assembly.read_text().splitlines()
        instructions = [line.strip() for line in lines if re.search(r"\b(?:LD_DEV|ST_DEV|ld_dev|st_dev|LD|LDG|LDP|STG|SHL|SHR)\b", line)]
        result["scalar_instruction_examples"] = instructions[:6]
        result["assembly_bytes"] = assembly.stat().st_size
    sst.write_json(report / "analysis.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
