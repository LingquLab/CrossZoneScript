"""Identify retained HCCL baseline coverage for the two-node EP32 campaign."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

from stage_sources import connect, run_remote

REMOTE = r'''set -u
hccl_dir=/usr/local/Ascend/cann-9.2.T101/tools/hccl_test
echo 'device count constants and validation'
python3 - "$hccl_dir" <<'LINUX'
from pathlib import Path
import re
import sys

files = sorted((Path(sys.argv[1]) / "common" / "src").glob("*"))
keys = ("SERVER_MAX_DEV_NUM", "dev_count", "npus >")
selected = []
for f in files:
    if not f.is_file() or f.suffix not in (".cc", ".h"):
        continue
    for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
        if any(key in line for key in keys):
            selected.append(f"{f.name}:{i} {line.strip()[:160]}")
for item in selected[:9]:
    print(item)
LINUX
for file in /etc/hccl_rootInfo.json /usr/local/mpich-3.2.1/bin/mpirun; do
    if test -r "$file"; then printf 'present=%s\n' "$file"; fi
done
'''


def inspect(host):
    with connect(host) as client:
        code, output, _ = run_remote(client, "bash -s", timeout=30, input_text=REMOTE)
        return f"{host} exit={code}\n{output}"


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=len(args["hosts"])) as pool:
        for result in pool.map(inspect, args["hosts"]):
            print(result)
