"""Run the blue-built HT MTE queue-state variant against the retained T4 inputs."""

import base64
import gzip
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root.parent / "alltoall"))
from ssh_transport import run

entry = Path(__file__).stem
resource_name = {
    "run_mte_metadata_t4": "mte_metadata_t4",
    "run_mte_scalar_t4": "mte_scalar_t4",
}.get(entry, "mte_t4")
resources = root / "resources" / resource_name
build = json.loads((resources / "build-record.json").read_text(encoding="utf-8"))
packed = (resources / "inter_server_send.o.gz").read_bytes()
assert hashlib.sha256(gzip.decompress(packed)).hexdigest() == build["kernel_sha256"]
source = (root / "run_mte_t4_remote.py").read_text(encoding="utf-8")
result = run(
    source,
    data={"build": build, "object": base64.b64encode(packed).decode("ascii")},
    timeout=2090,
)
print(json.dumps(result, ensure_ascii=True))
if result.get("run", {}).get("status") != "sst_and_checker_passed":
    raise SystemExit(1)
