"""Look for a matching HCCL Test record and installed binary on public_fp8."""
import json
from pathlib import Path
import shutil

home = Path.home()
sdk = Path("/usr/local/Ascend/cann-9.2.2")
roots = [home, home / "workspace", home / "work", home / "tilexr-validation", sdk / "tools"]
patterns = ("*hccl*test*", "*hccl*baseline*", "*all_reduce_test*")
seen = set()
binaries = set()
records = set()
for root in roots:
    if not root.is_dir():
        continue
    for pattern in patterns:
        for prefix in ("", "*/", "*/*/"):
            for match in root.glob(prefix + pattern):
                path = match.resolve()
                if path in seen:
                    continue
                seen.add(path)
                if path.name == "all_reduce_test" and path.is_file():
                    binaries.add(str(path))
                elif path.is_file() and path.suffix in (".log", ".json", ".md", ".txt"):
                    content = path.read_text(errors="replace")[:250000]
                    if any(word in content for word in ("aiv_only", "AIV-only", "aiv-only")):
                        records.add(path.name)
installed = shutil.which("all_reduce_test")
if installed:
    binaries.add(installed)
print("TILEXR_RESULT " + json.dumps({"aiv_only_record_files": sorted(records)[:10],
                                     "all_reduce_test": sorted(binaries)[:8],
                                     "sdk_source": (sdk / "tools/hccl_test/Makefile").is_file(),
                                     "mpirun": shutil.which("mpirun") is not None,
                                     "mpicxx": shutil.which("mpicxx") is not None}, separators=(",", ":")))
