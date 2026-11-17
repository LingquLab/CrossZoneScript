"""Read library identity, installed header fields and bounded CQ function load offsets."""

import base64
import ctypes
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
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


def cq_string_xrefs(path, disassembler):
    """Locate stripped CQ helpers by their diagnostic strings; return numeric observations."""
    data = path.read_bytes()
    assert data[:6] == b"\x7fELF\x02\x01"
    program_offset = struct.unpack_from("<Q", data, 32)[0]
    entry_size, entry_count = struct.unpack_from("<HH", data, 54)
    segments = []
    executable = []
    for index in range(entry_count):
        entry = struct.unpack_from("<IIQQQQQQ", data, program_offset + index * entry_size)
        if entry[0] == 1:
            segments.append((entry[2], entry[3], entry[5]))
            if entry[1] & 1:
                executable.append((entry[2], entry[3], entry[5]))
    strings = {}
    for needle in [b"RaHdcCtxCqCreate", b"RaHdcPrepareCqCreate", b"RaCtxPrepareCqCreate"]:
        start = 0
        while True:
            offset = data.find(needle + b"\0", start)
            if offset < 0:
                break
            for file_offset, virtual, size in segments:
                if file_offset <= offset < file_offset + size:
                    strings[virtual + offset - file_offset] = needle.decode()
            start = offset + 1
    process = subprocess.run([disassembler, "-d", str(path)], capture_output=True,
                             text=True, timeout=15)
    assert process.returncode == 0
    instructions = []
    for line in process.stdout.splitlines():
        match = re.match(r"\s*([0-9a-f]+):\s+[0-9a-f]{8}\s+(.+)", line)
        if match:
            instructions.append((int(match[1], 16), match[2]))
    string_references = []
    for file_offset, virtual, size in executable:
        for offset in range(file_offset, file_offset + size - 4, 4):
            encoded = struct.unpack_from("<I", data, offset)[0]
            kind = encoded & 0x9f000000
            if kind not in [0x10000000, 0x90000000]:
                continue
            register = encoded & 31
            immediate = ((encoded >> 29) & 3) | (((encoded >> 5) & 0x7ffff) << 2)
            if immediate & (1 << 20):
                immediate -= 1 << 21
            pc = virtual + offset - file_offset
            if kind == 0x10000000:
                target = pc + immediate
                if target in strings:
                    string_references.append((pc, target))
                continue
            page = (pc & ~4095) + (immediate << 12)
            for delta in range(1, 9):
                if offset + delta * 4 + 4 > file_offset + size:
                    break
                following = struct.unpack_from("<I", data, offset + delta * 4)[0]
                if following & 0xff000000 != 0x91000000 or (following >> 5) & 31 != register:
                    continue
                addition = ((following >> 10) & 0xfff) << (12 if following & (1 << 22) else 0)
                target = page + addition
                if target in strings:
                    string_references.append((pc, target))
    index_by_address = {address: index for index, (address, _) in enumerate(instructions)}
    located = []
    for reference, target in string_references:
        if reference not in index_by_address:
            continue
        index = index_by_address[reference]
        # The nearest preceding return bounds a code region, not a proven function.
        begin = index
        while begin > max(0, index - 240) and not instructions[begin - 1][1].startswith("ret"):
            begin -= 1
        end = index
        while end < min(len(instructions) - 1, index + 160) and not instructions[end][1].startswith("ret"):
            end += 1
        region = instructions[begin:end + 1]
        accesses = []
        for _, text in region:
            access = re.match(r"(ldr\w*|str\w*|ldp|stp)\s+([wx]\d+)(?:,\s*[wx]\d+)?"
                              r",\s*\[(x\d+|sp)(?:,\s*#(0x[0-9a-f]+|\d+))?\]", text)
            if access:
                accesses.append({"op": access[1], "width": access[2][0], "base": access[3],
                                 "offset": int(access[4] or "0", 0)})
        calls = [text.split("<", 1)[1].split(">", 1)[0].split("+", 1)[0]
                 for _, text in region if text.startswith("bl\t") and "<" in text]
        located.append({"diagnostic": strings[target], "instruction_count": len(region),
                        "accesses": accesses[:50], "named_calls": sorted(set(calls))[:18],
                        "extension_offset_accesses": [item for item in accesses
                                                      if item["offset"] in [36, 40]]})
    return {"diagnostic_strings_found": sorted(set(strings.values())), "regions": located[:2],
            "machine_reference_count": len(string_references),
            "parsed_instruction_count": len(instructions),
            "region_boundary_is_heuristic": True}

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
                                 "found": any(re.search("<" + name + r"(?:@@[^>]*)?>:", line)
                                              for line in lines),
                                 "loads": loads[:50]})

stripped_helpers = cq_string_xrefs(library_path, objdump) if objdump else {}

output = {"inspection_only": True, "npu_workload_run": False, "run_id": args["run_id"],
          "revision": saved["revision"], "library": library_identity, "headers": headers[:8],
          "objdump_available": objdump is not None, "binary_functions": binary_functions,
          "stripped_helpers": stripped_helpers,
          "resolution_context": "deployment_environment_probe_not_saved_process_map",
          "offsets_are_observations_not_a_forwarding_proof": True}
print("CZ_CQ_ABI_INSPECT " + json.dumps(output))
