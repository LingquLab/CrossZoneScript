# Copyright (c) 2026 TileXR Project.
"""Inspect the captured null-link fault and summarize model wiring evidence.

Reads session reports and installed model files without launching SST or changing
configuration. Detailed disassembly and source references stay in a new report.
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
    baseline = Path(json.loads((sst.WORK / "progress.json").read_text())["case"])
    assert baseline.parent == sst.WORK / "cases"
    report = Path(mkdtemp(prefix="wiring-inspect-", dir=baseline))
    snapshot = json.loads((baseline / "send-only/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    library = Path(snapshot["settings"]["library_dir"]) / "libUBusSimulator.so"
    symbol = "_ZN3SST7Davinci7BaLayer17sendReqToBusEventEPNS0_15BusPayloadEventE"
    disassembly = subprocess.run([shutil.which("objdump"), "-d", "--disassemble=" + symbol, str(library)],
                                 capture_output=True, text=True, timeout=30)
    (report / "sendReqToBusEvent.asm").write_text(disassembly.stdout)
    instructions = [line.strip() for line in disassembly.stdout.splitlines()
                    if re.match(r"^\s*[0-9a-f]+:\s+[0-9a-f]{8}\s", line)]
    result = dict(report=str(report), forwarding_instructions=instructions[:8], model_files=[])
    for path in sorted((sst.ROOT / "simulator").rglob("*.py")):
        text = path.read_text(errors="replace")
        if not re.search(r"BaLayer|ba_layer|baToBus|ba_to_bus", text):
            continue
        record = dict(path=str(path), matches=[])
        (report / (path.name + ".txt")).write_text(text)
        for number, line in enumerate(text.splitlines(), 1):
            if re.search(r"BaLayer|ba_layer|baToBus|ba_to_bus", line):
                record["matches"].append(dict(line=number, identifiers=re.findall(r"[A-Za-z_][A-Za-z_0-9]*", line)[:30]))
        try:
            tree = ast.parse(text)
            record["imports"] = [ast.unparse(node) for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
            record["link_calls"] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("addLink", "connect", "configureLink"):
                    constants = [value.value for value in ast.walk(node) if isinstance(value, ast.Constant) and isinstance(value.value, str)]
                    record["link_calls"].append(dict(line=node.lineno, method=node.func.attr, string_literals=constants[:12]))
            record["link_calls"] = record["link_calls"][:70]
        except SyntaxError:
            record["parse_error"] = True
        result["model_files"].append(record)
    configs = {}
    for name in ("UBusSim_config/UBusSim_simulator_config.yml", "v310_config/davidV100_simulator_config.yml"):
        config = sst.yaml.safe_load((baseline / "send-only" / name).read_text())
        configs[name] = {key: config[key] for key in ("simulator_info", "architecture_config") if key in config}
    result["configs"] = configs
    sst.write_json(report / "inspection.json", result)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
