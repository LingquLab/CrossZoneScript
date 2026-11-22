"""Map captured AIV exception PCs to the original Combine or barrier binary."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex
import sys

from stage_sources import connect, run_remote


REMOTE = r'''
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

original, attempt = map(Path, sys.argv[1:3])
cann = Path("/usr/local/Ascend/cann-9.2.T101")
pc_pair = re.compile(r'pc start:\s*(0x[0-9a-f]+),\s*current:\s*(0x[0-9a-f]+)', re.I)
kernels = re.compile(r'ubx_combine_write|tilexr_ep_ubx_barrier|tilexr_ep_ubx_combine_kernel', re.I)
offsets, names = Counter(), Counter()
error_details = []
for path in sorted(attempt.glob("rank-*/stderr.log")):
    text = path.read_bytes()[-180000:].decode(errors="replace")
    if "UBX_COMBINE_RUNTIME" not in text:
        continue
    block = text.split("UBX_COMBINE_RUNTIME", 1)[1].split("UBX_FAILURE", 1)[0]
    differences = {int(current, 16) - int(start, 16) for start, current in pc_pair.findall(block)}
    offsets.update(differences)
    names.update(set(kernels.findall(block)))
    for line in block.splitlines():
        if re.search(r'errorStr|error description|overflow|out of (?:bound|range)|error type|timeout|kernel.?name', line, re.I):
            if len(error_details) < 6:
                error_details.append(line.strip()[-183:])
tool_dirs = [cann / "bin", cann / "tools/ccec_compiler/bin", cann / "tools/bisheng_compiler/bin"]
available = []
for directory in tool_dirs:
    available.extend(str(p) for p in directory.glob("*objdump*") if p.is_file())
for name in ("llvm-objdump", "objdump"):
    tool = shutil.which(name)
    if tool and tool not in available:
        available.append(tool)
readelf = shutil.which("readelf")
binaries = {}
for kernel, file in (("ubx_combine_write", "tilexr_ep_ubx_combine_kernel.o"),
                     ("tilexr_ep_ubx_barrier", "tilexr_ep_ubx_kernel.o")):
    binary = original / "installs/stages-off/lib/tilexr/kernels" / file
    with binary.open("rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    symbols = subprocess.run([readelf, "-Ws", str(binary)], capture_output=True, text=True, timeout=12)
    symbol = None
    for line in symbols.stdout.splitlines():
        cols = line.split()
        if len(cols) >= 8 and cols[-1] == kernel:
            symbol = (int(cols[1], 16), int(cols[2]))
            break
    result = {"sha256": digest, "symbol": symbol}
    if symbol:
        base, size = symbol
        result["pc_inside"] = {hex(offset): 0 <= offset < size for offset, _ in offsets.most_common(10)}
        if size > 0 and offsets:
            for tool in available:
                if "msobjdump" in tool:
                    continue
                mapped = {}
                for offset, _ in offsets.most_common(4):
                    if not 0 <= offset < size:
                        continue
                    addr = base + offset
                    inspected = subprocess.run([tool, "-d", "--start-address=" + hex(addr),
                                                "--stop-address=" + hex(addr + 4), str(binary)],
                                               capture_output=True, text=True, timeout=12)
                    if inspected.returncode == 0:
                        instructions = [line.strip()[:125] for line in inspected.stdout.splitlines()
                                        if re.match(r'^\s*[0-9a-f]+:\s+\S', line, re.I)]
                        if instructions:
                            mapped[hex(offset)] = instructions[0]
                if mapped:
                    result["instruction"] = mapped
                    result["tool"] = tool
                    break
    binaries[kernel] = result
print(json.dumps({"pc_offsets_by_rank": {hex(k): v for k, v in offsets.most_common(18)},
                  "kernel_names": dict(names), "error_details": error_details,
                  "tools": available, "binaries": binaries}, separators=(",", ":")))
'''


def inspect(host, args):
    with connect(host) as client, client.open_sftp() as sftp:
        base = sftp.normalize(".") + "/.local/state/tilexr-ubx/"
        original = base + args["original"]
        attempt = base + args["namespace"] + "/" + args["attempt"]
        command = "/usr/bin/python3 - " + shlex.join([original, attempt])
        code, output, _ = run_remote(client, command, timeout=105, input_text=REMOTE)
        if code or not output:
            return {"host": host, "outcome": "pc_mapping_failed", "exit": code}
        return {"host": host, **json.loads(output)}


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", args[key]) for key in ("original", "namespace", "attempt")):
        raise ValueError("invalid audit identity")
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(lambda host: inspect(host, args), args["hosts"]):
            print(json.dumps(result, separators=(",", ":")))
