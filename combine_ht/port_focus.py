# Copyright (c) 2026 TileXR Project.
"""Read reserved BA station mappings and the captured fault's forwarding site.

Keeps complete native disassembly in the task report and returns only selected
configuration values, component names and short source references.
"""

import ast
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
    baseline = case / "send-only"
    snapshot = json.loads((baseline / "environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    report = Path(mkdtemp(prefix="port-focus-", dir=case))
    result = dict(report=str(report), mappings=[], definitions=[], model_files=[])
    wanted = re.compile(r"ccum_station|sllc_list|station_id|udie_id_list|ub_subsys.*(num|id)|body.*num", re.I)

    def collect(node, location):
        if isinstance(node, dict):
            for key, value in node.items():
                here = location + "/" + str(key)
                if wanted.search(str(key)):
                    result["mappings"].append(dict(path=here, value=value))
                else:
                    collect(value, here)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                collect(value, location + "/" + str(index))

    for name in ("UBusSim_config/UBusSim_simulator_config.yml", "v310_config/davidV100_simulator_config.yml", "v310_config/udie_noc_config.yml"):
        collect(sst.yaml.safe_load((baseline / name).read_text()), name)
    source = Path(snapshot["resources"]["installed_model.py"]["path"])
    tree = ast.parse(source.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(wanted.search(ast.unparse(target)) for target in targets):
                result["definitions"].append(dict(line=node.lineno, assignment=ast.unparse(node)))
    for path in sorted((sst.ROOT / "simulator/UBusSim_deployment").rglob("*.py")):
        text = path.read_text(errors="replace")
        if "def build_ba_layer" in text or "host_ccum_station_id" in text:
            result["model_files"].append(dict(path=str(path), matching_lines=[number for number, line in enumerate(text.splitlines(), 1) if "host_ccum_station_id" in line or "def build_ba_layer" in line]))
    library = Path(snapshot["settings"]["library_dir"]) / "libUBusSimulator.so"
    symbol = "_ZN3SST7Davinci7BaLayer17sendReqToBusEventEPNS0_15BusPayloadEventE"
    disassembly = subprocess.run([shutil.which("objdump"), "-d", "--disassemble=" + symbol, str(library)], capture_output=True, text=True, timeout=30).stdout
    (report / "forwarding.asm").write_text(disassembly)
    instructions = [line.strip() for line in disassembly.splitlines() if re.match(r"^\s*[0-9a-f]+:\s+[0-9a-f]{8}\s", line)]
    branches = [index for index, line in enumerate(instructions) if "send_impl" in line or "Link4send" in line]
    result["send_call_sites"] = [instructions[max(0, index - 3):index + 1] for index in branches[:1]]
    log = (baseline / "sim.log").read_text(errors="replace")
    result["last_component_names"] = re.findall(r"\[Element: ([^\]]+)\]", log)[-4:]
    sst.write_json(report / "inspection.json", result)
    assert len(json.dumps(result).encode()) < 8000
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
