"""Read model parameter documentation, loader choices and V310 bus wiring references."""
import json
from pathlib import Path
import re
import subprocess
import sys
import hashlib

sys.path.insert(0, "/work")
import sst

snapshot = json.loads(Path("/work/environment.snapshot.json").read_text())
out = dict(ok=True, diagnostic_only=True, read_only=True, parameter_info={}, loader_libraries=[], wiring=[])
env = snapshot["settings"]
commands = [("smmu", ["env", "LD_DEBUG=libs", "sst-info", "V310.Smmu"]),
            ("ba", ["env", "LD_DEBUG=libs", "sst-info", "UBusSimulator.BaLayer"])]
for label, argv in commands:
    p = subprocess.run(sst.shell_command(env, "sst", argv), capture_output=True, text=True, timeout=45)
    lines = p.stdout.splitlines()
    selected = []
    for index, line in enumerate(lines):
        if re.search(r"agent_mode|bond_mode|wr.*resp|bresp|UBusSim_flag", line, re.I):
            selected.append([x[:240] for x in lines[max(0,index-1):index+3]])
    out["parameter_info"][label] = dict(exit_code=p.returncode, relevant=selected[:5],
        missing_tool="not found" in p.stderr)
    for line in p.stderr.splitlines():
        match = re.search(r"calling init:\s*(/\S+)", line)
        if match and re.search(r"libV310|libDavinciSimulator|libUBusSimulator", match[1]):
            path = Path(match[1]).resolve()
            row = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            if row not in out["loader_libraries"]:
                out["loader_libraries"].append(row)
out["library_aliases"] = []
for name in ("libV310.so", "libDavinciSimulator.so", "libDavinciSimulatorV310.so", "libUBusSimulator.so"):
    path = Path(env["library_dir"]) / name
    out["library_aliases"].append(dict(name=name, exists=path.exists(), target=str(path.resolve())))
resource = Path(snapshot["resources"]["v310_config/davidV100_simulator_config.yml"]["path"])
root = resource.parents[2]
for path in sorted(root.rglob("*.py")):
    if path.stat().st_size > 512 * 1024:
        continue
    lines = path.read_text(errors="replace").splitlines()
    if not any("MultiChipTest" in line or "UBusSim_flag" in line for line in lines):
        continue
    hits = [dict(line=i+1, text=line.strip()[:260]) for i,line in enumerate(lines)
        if re.search(r"agent_mode|UBusSim_flag|bus.*rsp|rsp.*bus|wr.*rsp|class MultiChipTest",line,re.I)]
    if hits:
        out["wiring"].append(dict(path=str(path), hits=hits[:6]))
    if len(out["wiring"]) >= 5:
        break
print(json.dumps(out))
