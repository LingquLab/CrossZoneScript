"""Read version-matched LSU setup and device/MMIO store interface declarations."""
import json
from pathlib import Path
import re
import subprocess

out = dict(ok=True, diagnostic_only=True, read_only=True, generator_lsu=[], sdk_interfaces=[], model_memory_symbols=[])
generator = Path("/opt/sim/simulator/urma_dispatch/scripts/generate_dispatch_v3_prefilled_8p.py")
lines = generator.read_text(errors="replace").splitlines()
for index, line in enumerate(lines):
    if re.search(r"lsu_level|scalar.*cache|cache.*scalar|perf_config|v310_mode.*perf", line, re.I):
        out["generator_lsu"].append(dict(line=index + 1, context=[x[:220] for x in lines[max(0,index-1):index+2]]))
        if len(out["generator_lsu"]) >= 8:
            break
cann = Path("/opt/sim/Ascend/cann-9.1.0")
roots = [cann / "tools/bisheng_compiler/lib/clang/15.0.5/include",
         cann / "aarch64-linux/ascendc/include/simt_api", cann / "aarch64-linux/asc/include"]
for root in roots:
    if not root.exists():
        continue
    for path in sorted(root.rglob("*.h")):
        if path.stat().st_size > 2 * 1024 * 1024:
            continue
        hits = []
        for index, line in enumerate(path.read_text(errors="replace").splitlines()):
            if re.search(r"st_dev|store_dev|store.*mmio|uncached|set.*cache.?mode", line, re.I):
                hits.append(dict(line=index + 1, text=line.strip()[:260]))
                if len(hits) >= 4:
                    break
        if hits:
            out["sdk_interfaces"].append(dict(path=str(path), hits=hits))
        if len(out["sdk_interfaces"]) >= 8:
            break
    if len(out["sdk_interfaces"]) >= 8:
        break
lib = "/opt/sim/plugins/libDavinciSimulatorV310.so"
p = subprocess.run(["nm", "-D", "-S", "-C", lib], capture_output=True, text=True, timeout=30)
assert p.returncode == 0
patterns = (r"::ScalarStDev::", r"::ScalarIsa::.*(Rd|Wr|Mem|Req|Store)",
            r"::Lsu\w*::.*(Dev|Cache|Req|Store)", r"::Scalar\w*::.*(StDev|StoreDev|Device)")
for pattern in patterns:
    matches = [line.split(maxsplit=3)[-1][:220] for line in p.stdout.splitlines() if re.search(pattern,line)]
    out["model_memory_symbols"].append(dict(pattern=pattern, names=matches[:14]))
print(json.dumps(out))
