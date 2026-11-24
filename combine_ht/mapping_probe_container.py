"""Collect only address-map settings and relevant model call/shift evidence."""
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import yaml

CASE = Path("/work/cases/8p-bs16-a0840f02-a1/send-only")
out = {"ok": True, "diagnostic_only": True, "read_only": True, "mapping_fields": [], "libraries": []}


def fields(node, path):
    if isinstance(node, dict):
        for key, value in node.items():
            fields(value, path + "/" + str(key))
    elif isinstance(node, list):
        if re.search(r"addr|doorbell|jetty|mmio|ta_|_ta|_base|_range", path, re.I):
            out["mapping_fields"].append({"path": path, "value": str(node)[:240]})
    elif re.search(r"addr|doorbell|jetty|mmio|ta_|_ta|_base|_range", path, re.I):
        out["mapping_fields"].append({"path": path, "value": str(node)[:240]})


for directory in ("v310_config", "UBusSim_config"):
    for path in sorted((CASE / directory).glob("*.yml")):
        fields(yaml.safe_load(path.read_text()), directory + "/" + path.name)
out["mapping_field_count"] = len(out["mapping_fields"])
out["mapping_fields"] = [{"path": row["path"][-220:], "value": row["value"][:120]}
                         for row in out["mapping_fields"][:60]]
raw = (CASE / "input_bin/rank3/sq_ctx.bin").read_bytes()
out["fault_queue"] = {"rank": 3, "flat": 82, "doorbell": hex(struct.unpack_from("<Q", raw, 82 * 80 + 48)[0])}

for filename in ("libDavinciSimulatorV310.so", "libUBusSimulator.so"):
    library = Path("/opt/sim/plugins") / filename
    result = {"name": filename, "sha256": hashlib.sha256(library.read_bytes()).hexdigest(), "methods": []}
    p = subprocess.run(["nm", "-D", "-S", "-C", str(library)], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, "model_symbols_unavailable"
    symbols = []
    for line in p.stdout.splitlines():
        words = line.split(maxsplit=3)
        if len(words) != 4 or not re.fullmatch(r"[0-9a-fA-F]+", words[0]) or not re.fullmatch(r"[0-9a-fA-F]+", words[1]):
            continue
        name = words[3]
        if re.search(r"ScalarStDev::(decode|execute|issue\w*)\(|BaLayer::processBaFromBusRdReqBuffer\(|AddrAnalyzer.*(getAddrType|getDdieId|getDstType)|AddrResolver|AddrResolution", name):
            symbols.append((int(words[0], 16), int(words[1], 16), name))
    result["address_symbols"] = [line.split(maxsplit=3)[-1][:160] for line in p.stdout.splitlines()
        if re.search(r"Addr.*(Type|Decode|Resol|TA|Ta)|getDstType", line)][:20]
    for address, size, name in symbols[:6]:
        p = subprocess.run(["objdump", "-d", "-C", "--start-address=" + str(address),
            "--stop-address=" + str(address + size), str(library)], capture_output=True, text=True, timeout=20)
        assert p.returncode == 0, "model_disassembly_unavailable"
        lines = p.stdout.splitlines()
        calls = [line.split("<", 1)[1].rsplit(">", 1)[0][:240] for line in lines if re.search(r"\bbl\b", line) and "<" in line]
        shifts = [line.strip() for line in lines if re.search(r"\blsl\b|\blsr\b|\bubfx\b|\bubfiz\b", line)]
        result["methods"].append({"name": name[:180], "address": hex(address), "size": size,
            "calls": [x[:160] for x in list(dict.fromkeys(calls))[:10]],
            "shifts": [x[:120] for x in shifts[:4]]})
    out["libraries"].append(result)
print(json.dumps(out))
