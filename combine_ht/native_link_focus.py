# Copyright (c) 2026 TileXR Project.
"""Inspect native BA link initialization and save disassembly locally.

Reads only the pinned simulator library. Returns short instruction windows for
the null link field and relevant link names without running simulator events.
"""

import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import mkdtemp

import sst


def main():
    case = Path(json.loads((sst.WORK / "progress.json").read_text())["case"])
    assert case.parent == sst.WORK / "cases"
    snapshot = json.loads((case / "send-only/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    report = Path(mkdtemp(prefix="native-link-", dir=case))
    library = Path(snapshot["settings"]["library_dir"]) / "libUBusSimulator.so"
    symbols = subprocess.run(["nm", "-D", "--defined-only", str(library)], capture_output=True, text=True, timeout=20).stdout
    constructors = [line.split()[-1] for line in symbols.splitlines() if re.search(r"BaLayerC[12]E", line)]
    result = dict(report=str(report), library_sha256=sst.checksum(library), constructor_symbols=constructors, field_accesses=[])
    for symbol in constructors[:1]:
        disassembly = subprocess.run([shutil.which("objdump"), "-d", "--disassemble=" + symbol, str(library)], capture_output=True, text=True, timeout=30).stdout
        (report / "constructor.asm").write_text(disassembly)
        lines = [line.strip() for line in disassembly.splitlines() if re.match(r"^\s*[0-9a-f]+:\s+[0-9a-f]{8}\s", line)]
        for index, line in enumerate(lines):
            if re.search(r"#(?:4120|0x1018)\]", line):
                result["field_accesses"].append(dict(address=line.split(":")[0], window=lines[max(0, index-3):index+1]))
    strings = subprocess.run(["strings", "-t", "x", str(library)], capture_output=True, text=True, timeout=20).stdout.splitlines()
    result["native_port_names"] = [line.strip() for line in strings if re.search(r"\b(?:ubSubsysReqPort|netSimReqPort|V310_flag|UBusSim_flag|simt_urma_flag)\b", line)]
    result["field_accesses"] = result["field_accesses"][:2]
    sst.write_json(report / "analysis.json", result)
    assert len(json.dumps(result).encode()) < 6000
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
