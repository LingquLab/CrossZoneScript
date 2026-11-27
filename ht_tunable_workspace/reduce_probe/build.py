"""Compile the two SST probe entries with the original BF16 Combine build flags."""
import gzip
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from instrument import write_headers

here = Path(__file__).resolve().parent
recipe = Path(sys.argv[1]) / "src/moonep/combine_ht/CMakeFiles/tilexr_moonep_combine_ht_kernel.dir/build.make"
lines = recipe.read_text().splitlines()
compiler, linker = (shlex.split(next(line.split(" && ", 1)[1].strip() for line in lines if match in line))
                    for match in ("/bin/bisheng -xcce", "/bin/ld.lld -m aicorelinux"))
source = Path(compiler[compiler.index("-c") + 1])
checkout = source.parent
revision = subprocess.check_output(["git", "-C", checkout, "rev-parse", "HEAD"], text=True).strip()
assert revision == "186dfa18a0358add6af8ef237ebca23edffa86f5"
assert not any("FP32=1" in arg for arg in compiler)
trace = "--trace" in sys.argv[2:]
with tempfile.TemporaryDirectory(prefix="tilexr-reduce-probe-") as scratch:
    raw = Path(scratch) / "probe.o"
    rel = Path(scratch) / "probe_rel.o"
    includes = ["-I" + str(source.parent)]
    if trace:
        write_headers(Path(scratch), Path(subprocess.check_output(
            ["git", "-C", checkout, "rev-parse", "--show-toplevel"], text=True).strip()))
        includes.insert(0, "-I" + scratch)
    subprocess.run(compiler[:compiler.index("-c")] + includes + ["-c", str(here / "probe.cpp"),
                                                     "-o", str(rel)], check=True)
    subprocess.run(linker[:linker.index("-Ttext=0") + 1] + [str(rel), "--static", "--allow-multiple-definition",
                                                           "-o", str(raw)], check=True)
    native = raw.read_bytes()
assert len(native) < 10 * 1024**2
padded = native.ljust(10 * 1024**2, b"\0")
(here / ("trace.o.gz" if trace else "probe.o.gz")).write_bytes(gzip.compress(padded, mtime=0))
record = {"revision": revision, "elf_sha256": hashlib.sha256(native).hexdigest(),
          "object_sha256": hashlib.sha256(padded).hexdigest()}
(here / ("trace-object.json" if trace else "object.json")).write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record))
