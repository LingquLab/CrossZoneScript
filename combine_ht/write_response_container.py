"""Read the first receiver response failure and its model protocol settings."""
import json
from pathlib import Path
import re
import subprocess
import yaml

CASE = Path("/work/diagnostics/mmio-mte-a1/mmio-mte4")
out = dict(ok=True, diagnostic_only=True, read_only=True, settings=[], receiver_handler={}, memory_symbols=[])


def flags(node, path):
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(value, (dict, list)):
                relevant = re.search(r"wr|write|rsp|resp|ack|bond|packing|simt_urma", str(key), re.I)
                if relevant and not re.search(r"latency|credit|depth|delay|bandwidth|init", str(key), re.I):
                    out["settings"].append(dict(path=path + "/" + str(key), value=str(value)[:160]))
            flags(value, path + "/" + str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            flags(value, path + "/" + str(index))


for filename in ("UBusSim_config/UBusSim_simulator_config.yml", "v310_config/davidV100_simulator_config.yml"):
    flags(yaml.safe_load((CASE / filename).read_text()), filename)
out["setting_count"] = len(out["settings"])
out["settings"] = out["settings"][:55]
for filename in ("libUBusSimulator.so", "libDavinciSimulator.so", "libDavinciSimulatorV310.so"):
    lib = Path("/opt/sim/plugins") / filename
    p = subprocess.run(["nm", "-D", "-S", "-C", str(lib)], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0
    names = p.stdout.splitlines()
    out["memory_symbols"].append(dict(library=filename, names=[line.split(maxsplit=3)[-1][:200] for line in names
        if re.search(r"BResp|BRsp|WrRsp|WriteResp|Bond|::Lsu|::LSU|wrResp|writeResp", line)][:22]))
    if filename != "libUBusSimulator.so":
        continue
    for line in names:
        if "::BaLayer::recvRspFromBusEvent(" not in line:
            continue
        parts = line.split(maxsplit=3)
        address, size = int(parts[0], 16), int(parts[1], 16)
        p = subprocess.run(["objdump", "-d", "-C", "--start-address=" + str(address),
            "--stop-address=" + str(address + size), str(lib)], capture_output=True, text=True, timeout=30)
        assert p.returncode == 0
        lines = [x.strip() for x in p.stdout.splitlines() if re.match(r"\s*[0-9a-f]+:", x)]
        contexts = []
        for index, text in enumerate(lines):
            if "SST::Output::fatal" in text:
                contexts.append(lines[max(0,index-7):index+1])
        calls = [x.split("<",1)[1].rsplit(">",1)[0][:180] for x in lines if re.search(r"\bbl\b",x) and "<SST::Davinci::" in x]
        out["receiver_handler"] = dict(address=hex(address), size=size,
            type_comparisons=[x for x in lines if re.search(r"\bcmp\b|\bldrb\b",x)][:8],
            fatal_contexts=contexts[:2], model_calls=list(dict.fromkeys(calls))[:14])
        break
    p = subprocess.run(["strings", str(lib)], capture_output=True, text=True, timeout=30)
    out["protocol_parameter_names"] = [x[:220] for x in p.stdout.splitlines()
        if re.search(r"wr.*bond|bond.*wr|bresp|write.*resp|wr.*resp", x, re.I)][:15]
snapshot = json.loads(Path("/work/environment.snapshot.json").read_text())
out["selected_model_entry"] = snapshot["resources"]["installed_model.py"]["path"]
print(json.dumps(out))
