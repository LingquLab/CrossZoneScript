"""Compile against the BF16-independent FinalReduce FP32 production worker."""
import gzip
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

here = Path(__file__).resolve().parent
source = Path(sys.argv[1]).resolve()
revision = subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip()
assert not subprocess.check_output(["git", "-C", source, "status", "--porcelain"], text=True)
recipe = (Path(sys.argv[2]) / "src/ep/combine_ht/CMakeFiles"
          / "tilexr_ep_ht_final_reduce_sst_kernel.dir/build.make").read_text().splitlines()
compiler, linker = (shlex.split(next(line.split(" && ", 1)[1].strip() for line in recipe if match in line))
                    for match in ("/bin/bisheng -xcce", "/bin/ld.lld -m aicorelinux"))
flags = [flag for flag in compiler[:compiler.index("-c")]
         if not flag.startswith("-DTILEXR_ENABLE_PROFILING") and "/tmp/tilexr-ht-tunable-" not in flag]
with tempfile.TemporaryDirectory(prefix="tilexr-final-reduce-bench-") as tmp:
    rel, elf = Path(tmp) / "probe-rel.o", Path(tmp) / "probe.o"
    subprocess.run(flags + ["-I" + str(source / "include"), "-I" + str(source / "src/ep/combine_ht"),
                            "-c", str(here / "probe.cpp"), "-o", str(rel)], check=True)
    subprocess.run(linker[:linker.index("-Ttext=0") + 1] + [str(rel), "--static", "--allow-multiple-definition",
                                                           "-o", str(elf)], check=True)
    native = elf.read_bytes()
assert len(native) < 10 * 1024**2
binary = native.ljust(10 * 1024**2, b"\0")
(here / "probe.o.gz").write_bytes(gzip.compress(binary, mtime=0))
meta = dict(revision=revision, object_sha256=hashlib.sha256(binary).hexdigest())
(here / "object.json").write_text(json.dumps(meta, indent=2) + "\n")
print(json.dumps(meta))
