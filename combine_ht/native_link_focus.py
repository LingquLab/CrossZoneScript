# Copyright (c) 2026 TileXR Project.
"""Inspect native BA link initialization and save disassembly locally.

Reads only the pinned simulator library. Returns short instruction windows for
the null link field and relevant link names without running simulator events.
"""

from contextlib import redirect_stdout
import io
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
    disassembly = subprocess.run([shutil.which("objdump"), "-d", str(library)], capture_output=True, text=True, timeout=60).stdout
    (report / "library.asm").write_text(disassembly)
    function = None
    functions, accesses, recent = [], [], []
    for line in disassembly.splitlines():
        match = re.match(r"^[0-9a-f]+ <([^>]+)>:", line)
        if match:
            function = match[1]
            recent = []
            if "_ZN3SST7Davinci7BaLayer" in function:
                functions.append(function)
        if not re.match(r"^\s*[0-9a-f]+:\s+[0-9a-f]{8}\s", line):
            continue
        if function and "_ZN3SST7Davinci7BaLayer" in function:
            if re.search(r"#(?:4120|4112|0x1018|0x1010)\]", line):
                accesses.append(dict(function=function, instruction=line.strip(), previous=recent[-4:]))
            recent.append(line.strip())
    result["ba_methods"] = functions[:60]
    result["field_accesses"] = [{"function": item["function"], "instruction": item["instruction"]} for item in accesses]
    writes = [item for item in accesses if re.search(r"\s(?:str|stp)\s", item["instruction"])]
    if writes:
        result["initialization_window"] = writes[-1]["previous"] + [writes[-1]["instruction"]]
    sst.write_json(report / "field-accesses.json", accesses)
    strings = subprocess.run(["strings", "-t", "x", str(library)], capture_output=True, text=True, timeout=20).stdout.splitlines()
    result["native_port_names"] = [line.strip() for line in strings if re.search(r"\b(?:ubSubsysReqPort|netSimReqPort|V310_flag|UBusSim_flag|simt_urma_flag)\b", line)]
    result["field_accesses"] = result["field_accesses"][:12]
    source_paths = [str(path) for path in (sst.ROOT / "simulator/UBusSim_deployment").rglob("*")
                    if path.suffix in (".h", ".cpp", ".cc") and re.search(r"ba.?layer", path.name, re.I)]
    result["native_source_paths"] = source_paths[:8]
    sst.write_json(report / "analysis.json", result)
    assert len(json.dumps(result).encode()) < 8000
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
