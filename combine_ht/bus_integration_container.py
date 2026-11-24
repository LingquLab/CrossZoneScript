"""Inspect client integration flags and the installed bus response interface names."""
import json
from pathlib import Path
import re
import subprocess
import yaml

CASE = Path("/work/diagnostics/mmio-mte-a1/mmio-mte4")
out = dict(ok=True, diagnostic_only=True, read_only=True, flags=[], model_identifiers=[], entry_references=[])


def walk(node, path):
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(value, (dict, list)) and re.search(r"flag|mode|enable|ubus|urma|simt|version|component", str(key), re.I):
                if not re.search(r"dump|stat|trace|log|icache|bank|ecc", str(key), re.I):
                    out["flags"].append(dict(path=path + "/" + str(key), value=str(value)[:200]))
            walk(value, path + "/" + str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            walk(value, path + "/" + str(index))


for filename in ("v310_config/davidV100_simulator_config.yml", "UBusSim_config/UBusSim_simulator_config.yml"):
    walk(yaml.safe_load((CASE / filename).read_text()), filename)
out["flag_count"] = len(out["flags"])
out["flags"] = out["flags"][:100]
for filename in ("libDavinciSimulator.so", "libDavinciSimulatorV310.so", "libUBusSimulator.so"):
    path = Path("/opt/sim/plugins") / filename
    p = subprocess.run(["strings", str(path)], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0
    identifiers = [line for line in p.stdout.splitlines() if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{1,100}", line)
                   and re.search(r"ubus|urma|bresp|wr.*resp|response.*type|rsp.*type|bond", line, re.I)]
    out["model_identifiers"].append(dict(library=filename, names=list(dict.fromkeys(identifiers))[:35]))
snapshot = json.loads(Path("/work/environment.snapshot.json").read_text())
entry = Path(snapshot["resources"]["installed_model.py"]["path"])
paths = [entry, entry.parent / "simulator/UB_simulator.py"]
paths += sorted((entry.parent / "simulator").glob("*v310*.py"))
paths += sorted((entry.parent / "simulator").glob("*V310*.py"))
for path in paths:
    if not path.is_file():
        continue
    hits = []
    for index, line in enumerate(path.read_text(errors="replace").splitlines()):
        if re.search(r"UBusSim_flag|ubus_flag|with_ubus|Wr.*Rsp|wr.*rsp|v310.*flag", line, re.I):
            hits.append(dict(line=index + 1, text=line.strip()[:260]))
    out["entry_references"].append(dict(path=str(path), hits=hits[:6]))
print(json.dumps(out))
