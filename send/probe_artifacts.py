"""Return bounded symbol metadata and function hashes from retained ELF files."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess


def inspect(folder, symbol_fragment="ProbeOperation"):
    folder = Path(folder)
    try:
        record = json.loads((folder / "trial-result.json").read_text())
        binary = folder / "send.o"
        assert 0 < binary.stat().st_size < 64 * 1024 * 1024
        blob = binary.read_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        assert digest == record["object_sha256"]
        assert blob[:6] == b"\x7fELF\x02\x01", "ELF64 little-endian required"
        section_offset = struct.unpack_from("<Q", blob, 40)[0]
        section_entry_size, section_count = struct.unpack_from("<HH", blob, 58)
        assert section_entry_size >= 64
        symbols = subprocess.run(["readelf", "-sW", str(binary)], capture_output=True,
                                 text=True, timeout=15, check=True).stdout
        functions = []
        for line in symbols.splitlines():
            fields = line.split()
            if len(fields) < 8 or fields[3] != "FUNC" or symbol_fragment not in fields[7]:
                continue
            if symbol_fragment == "BuildPutWqes" and not fields[7].endswith(".vector.thread"):
                continue
            name, size = fields[7], int(fields[2], 0)
            row = {"name": name[:260], "size": size}
            if fields[6].isdigit() and int(fields[6]) < section_count:
                section = struct.unpack_from("<IIQQQQIIQQ", blob,
                                             section_offset + int(fields[6]) * section_entry_size)
                address = int(fields[1], 16)
                start = section[4] + address - section[3]
                if 0 < size and address >= section[3] and start + size <= section[4] + section[5]:
                    row["code_sha256"] = hashlib.sha256(blob[start:start + size]).hexdigest()
            if shutil.which("c++filt"):
                symbol = name[:-len(".vector.thread")] if name.endswith(".vector.thread") else name
                demangled = subprocess.run(["c++filt", symbol],
                                           capture_output=True, text=True, timeout=10)
                if demangled.returncode == 0:
                    row["demangled"] = demangled.stdout.strip()[:380]
            functions.append(row)
            if len(functions) == 3:
                break
        return {"available": True, "folder": folder.name, "object_sha256": digest,
                "functions": functions}
    except Exception as error:
        return {"available": False, "folder": folder.name, "error": type(error).__name__}
