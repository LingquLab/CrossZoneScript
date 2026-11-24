"""Inspect the failed BA read gate, selected cache settings and model flag references."""
import json
from pathlib import Path
import re
import subprocess
import yaml

out = dict(ok=True, diagnostic_only=True, read_only=True, ba_type_gate=[], cache_settings=[], flag_references=[])
case = Path("/work/cases/8p-bs16-a0840f02-a1/send-only")
config = yaml.safe_load((case / "v310_config/davidV100_simulator_config.yml").read_text())


def cache_fields(node, path):
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(value, (dict, list)) and re.search(r"cache|bypass|scalar|lsu|mem_type|mem_attr", str(key), re.I):
                out["cache_settings"].append(dict(path=path + "/" + str(key), value=str(value)[:160]))
            cache_fields(value, path + "/" + str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            cache_fields(value, path + "/" + str(index))


cache_fields(config, "v310")
out["cache_settings"] = out["cache_settings"][:45]
library = Path("/opt/sim/plugins/libUBusSimulator.so")
p = subprocess.run(["objdump", "-d", "-C", "--start-address=0x14ff54", "--stop-address=0x150670", str(library)],
                   capture_output=True, text=True, timeout=30)
assert p.returncode == 0
instructions = [line.strip() for line in p.stdout.splitlines() if re.match(r"\s*[0-9a-f]+:", line)]
for index, line in enumerate(instructions):
    if "getBaAddrTableType" in line:
        out["ba_type_gate"].append(instructions[index:index + 8])
out["ba_comparisons"] = [line for line in instructions if re.search(r"\bcmp\b.*#0x[0-9]+\b|\bcb[n]?z\b", line)][:8]
out["ba_flag_loads"] = [line for line in instructions if re.search(r"\bldrb\b", line)][:6]
snapshot = json.loads(Path("/work/environment.snapshot.json").read_text())
roots = {Path(snapshot["resources"]["installed_model.py"]["path"]).parent, Path("/opt/sim/simulator")}
files = []
for root in roots:
    for pattern in ("*.py", "*.md", "*.txt"):
        for path in root.rglob(pattern):
            if len(files) >= 1200:
                break
            if path not in files and path.stat().st_size <= 512 * 1024:
                files.append(path)
for path in files:
    lines = path.read_text(errors="replace").splitlines()
    for index, line in enumerate(lines):
        if "simt_urma_flag" in line or ("st_dev" in line and "doorbell" in line.lower()):
            out["flag_references"].append(dict(path=str(path), line=index + 1,
                context=[x[:300] for x in lines[max(0, index - 1):index + 2]]))
            if len(out["flag_references"]) >= 6:
                break
    if len(out["flag_references"]) >= 6:
        break
out["searched_files"] = len(files)
print(json.dumps(out))
