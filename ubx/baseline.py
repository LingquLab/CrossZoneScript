"""Identify retained HCCL baseline coverage for the two-node EP32 campaign."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

from stage_sources import connect, run_remote

REMOTE = r'''set -u
hccl_dir=/usr/local/Ascend/cann-9.2.T101/tools/hccl_test
echo 'source layout'
find "$hccl_dir" -maxdepth 4 -type f \( -name '*.cpp' -o -name '*.cc' -o -name '*.h' \) -printf '%P\n' | head -55
echo 'device mapping and checks'
rg -n -i -g '*.cc' -g '*.cpp' -g '*.h' 'HCCL_TEST_USE_DEVS|g_.*dev(num|icecount)|numDev.*>[[:space:]]*(8|16)|ndevs.*(max|limit|rang)|npus.*(max|limit|rang)|dev(num|icecount).*(max|limit|rang)' "$hccl_dir" 2>/dev/null | cut -c1-245 | head -35
echo 'binary diagnostics'
strings "$hccl_dir/bin/all_reduce_test" | grep -iE 'npus|device num|HCCL_TEST_USE_DEVS|rank number|out of range' | cut -c1-190 | head -22
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
