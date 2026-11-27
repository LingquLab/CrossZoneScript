"""Read diagnostics from a completed standalone load probe without rerunning SST."""
import json
from pathlib import Path
import sys

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here.parents[1] / "alltoall"))
from ssh_transport import run

options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
source = r"""
import json
from pathlib import Path
import re
import sys
label = json.load(sys.stdin)["label"]
assert re.fullmatch(r"unpack-b16(?:-[a-z0-9-]+)?", label)
case = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/cast-v310-repair-0784b139") / label
result = json.loads((case / "result.json").read_text())
log = case / "check.log"
if log.is_file():
    lines = log.read_text().splitlines()
    result["checker_last"] = lines[-1][:320] if lines else ""
result["raw_dumps"] = {p.name: p.stat().st_size for p in (case / "bin").glob("david_0_die*_raw.bin")}
print(json.dumps(result, separators=(",", ":")))
"""
print(json.dumps(run(source, data=options, password=True), separators=(",", ":")))
