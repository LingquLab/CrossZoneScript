"""Read library identity, installed header fields and bounded CQ function load offsets."""

import base64
import ctypes
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


args = json.loads(base64.urlsafe_b64decode(sys.argv[1]))
assert re.fullmatch(r"pr229-[A-Za-z0-9_-]{1,58}", args["run_id"])
parent = Path("/home/c00996840/ai/TileXR-test").resolve(strict=True)
work = (parent / args["run_id"]).resolve(strict=True)
assert work.parent == parent
saved = json.loads((work / "result.json").read_text())
assert saved["devices"] == [2, 3] and saved["revision"] == args["revision"]


class DlInfo(ctypes.Structure):
    _fields_ = [("filename", ctypes.c_char_p), ("base", ctypes.c_void_p),
                ("symbol", ctypes.c_char_p), ("symbol_address", ctypes.c_void_p)]


# Resolve libra.so in the deployment environment. No RA, device or CQ API is called.
library = ctypes.CDLL("libra.so")
dl = ctypes.CDLL("libdl.so.2")
dl.dladdr.argtypes = [ctypes.c_void_p, ctypes.POINTER(DlInfo)]
dl.dladdr.restype = ctypes.c_int
info = DlInfo()
assert dl.dladdr(ctypes.cast(library.RaCtxCqCreate, ctypes.c_void_p), ctypes.byref(info)) != 0
library_path = Path(info.filename.decode()).resolve(strict=True)
assert library_path.name == "libra.so" or library_path.name.startswith("libra.so.")
library_identity = {"path": str(library_path),
                    "sha256": hashlib.sha256(library_path.read_bytes()).hexdigest()}

headers = []
for root in [Path("/usr/local/Ascend/cann-9.1.0/aarch64-linux/include"),
             Path("/usr/local/Ascend/driver/include")]:
    if not root.is_dir():
        continue
    for path in sorted(root.rglob("*hccp*ctx*.h"))[:12]:
        content = path.read_text(errors="replace")
        fields = [line.strip()[:200] for line in content.splitlines()
                  if re.search(r"\b(?:ccuExCfg|cqeFlag|JFC_MODE_CCU_POLL)\b", line)]
        headers.append({"path": str(path.resolve()),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "has_ccu_extension": "ccuExCfg" in content,
                        "selected_declarations": fields[:3]})

binary_functions = []
objdump = shutil.which("objdump")
if objdump:
    for name in ["RaCtxCqCreate", "RaHdcCtxCqCreate", "RaHdcPrepareCqCreate", "RaCtxPrepareCqCreate"]:
        process = subprocess.run([objdump, "-d", "--disassemble=" + name, str(library_path)],
                                 capture_output=True, text=True, timeout=8)
        lines = process.stdout.splitlines()
        loads = []
        for line in lines:
            match = re.search(r"\b(ldrb|ldrh|ldr|ldp)\s+([wx]\d+)(?:,\s*[wx]\d+)?"
                              r",\s*\[(x\d+|sp)(?:,\s*#(0x[0-9a-fA-F]+|\d+))?\]", line)
            if match:
                loads.append({"op": match[1], "width": match[2][0], "base": match[3],
                              "offset": int(match[4] or "0", 0)})
        binary_functions.append({"name": name, "exit_code": process.returncode,
                                 "found": any("<" + name + ">:" in line for line in lines),
                                 "loads": loads[:50]})

output = {"inspection_only": True, "npu_workload_run": False, "run_id": args["run_id"],
          "revision": saved["revision"], "library": library_identity, "headers": headers[:8],
          "objdump_available": objdump is not None, "binary_functions": binary_functions,
          "resolution_context": "deployment_environment_probe_not_saved_process_map",
          "offsets_are_observations_not_a_forwarding_proof": True}
print("CZ_CQ_ABI_INSPECT " + json.dumps(output))
