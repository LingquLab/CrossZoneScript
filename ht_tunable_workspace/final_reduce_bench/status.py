"""Inspect the completed prefix of the standalone SST timing matrix."""
import json
from pathlib import Path
import sys

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here.parents[1] / "alltoall"))
from ssh_transport import run

options = json.loads(Path(sys.argv[2]).read_text())
source = r"""
import json
from pathlib import Path
import re
import sys
label = json.load(sys.stdin)["label"]
assert re.fullmatch(r"final-reduce-bench(?:-[a-z0-9-]+)?", label)
root = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/cast-v310-repair-0784b139") / label
record = root / "result.json"
result = json.loads(record.read_text()) if record.exists() else {"rows": []}
result["case_dirs"] = sorted(p.name for p in root.glob("t*") if p.is_dir())
print(json.dumps(result, separators=(",", ":")))
"""
print(json.dumps(run(source, data=options, password=True, timeout=50), separators=(",", ":")))
