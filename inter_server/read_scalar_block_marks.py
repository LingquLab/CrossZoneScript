"""Look for logical block launch identities in the passing T4 model outputs."""

import json
import subprocess

source = r"""
from collections import Counter
import json
from pathlib import Path
import re

root = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native")
case = root / "t4-sst-mte-scalar-forward-5a6c7c8c"
expected = "5a6c7c8cac0b13c99d824725df3e73d91bacbeff576693b4823e2705ab50ffbd"
run = json.loads((case / "run.json").read_text())
assert run["status"] == "sst_and_checker_passed" and run["kernel_sha256"] == expected

patterns = {
    "logical_block_id": re.compile(r"block[_ .-]?(?:id|idx|index)\b", re.I),
    "dispatch_id": re.compile(r"(?:task|launch|dispatch)[_ .-]?id\b", re.I),
    "kernel_launch": re.compile(r"(?:kernel|block)[_ .-]+(?:launch(?:ed)?|start(?:ed)?)\b", re.I),
    "physical_core_id": re.compile(r"aicoreId_|subcoreId_|coreId_", re.I),
}
summary = {}
for path in (case / "sim.log", case / "native-runner.log", *sorted((case / "OUTPUT").rglob("*"))):
    if not path.is_file():
        continue
    data = path.read_bytes()[:4 * 1024 * 1024] if path.stat().st_size <= 8 * 1024 * 1024 else b""
    if not data:
        continue
    content = data.decode("utf-8", "replace")
    counts = {key: len(pattern.findall(content)) for key, pattern in patterns.items()}
    extension = path.suffix or "<none>"
    result = summary.setdefault(extension, Counter())
    result.update(files=1, **counts)
print(json.dumps({"run_status": run["status"], "examined_by_extension": summary}, ensure_ascii=True))
"""
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode("ascii"), capture_output=True, timeout=70)
proc.check_returncode()
print(json.dumps(json.loads(proc.stdout), ensure_ascii=True))
