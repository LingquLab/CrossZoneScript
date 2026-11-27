"""Build the probe with the same CANN architecture and flags as BF16 Combine."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tempfile

here = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("combine_build", type=Path)
args = parser.parse_args()
recipe = (args.combine_build / "src/moonep/combine_ht/CMakeFiles"
          / "tilexr_moonep_combine_ht_kernel.dir/build.make").read_text().splitlines()
compiler, linker = (shlex.split(next(line.split(" && ", 1)[1].strip() for line in recipe if match in line))
                    for match in ("/bin/bisheng -xcce", "/bin/ld.lld -m aicorelinux"))
assert "--cce-aicore-arch=dav-c310-vec" in compiler
flags = compiler[:compiler.index("-c")]
with tempfile.TemporaryDirectory(prefix="tilexr-unpack-b16-") as scratch:
    obj = Path(scratch) / "rel.o"
    elf = Path(scratch) / "probe.o"
    subprocess.run(flags + ["-c", str(here / "probe.cpp"), "-o", str(obj)], check=True)
    subprocess.run(linker[:linker.index("-Ttext=0") + 1] + [str(obj), "--static", "--allow-multiple-definition",
                                                           "-o", str(elf)], check=True)
    native = elf.read_bytes()

assert len(native) < 10 * 1024**2
binary = native.ljust(10 * 1024**2, b"\0")
(here / "probe.o.gz").write_bytes(gzip.compress(binary, mtime=0))
metadata = {"elf_sha256": hashlib.sha256(native).hexdigest(), "object_sha256": hashlib.sha256(binary).hexdigest(),
            "arch": "dav-c310-vec"}
(here / "object.json").write_text(json.dumps(metadata, indent=2) + "\n")
print(json.dumps(metadata))
