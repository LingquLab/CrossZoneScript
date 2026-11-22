"""Identify retained HCCL baseline coverage for the two-node EP32 campaign."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

from stage_sources import connect, run_remote

REMOTE = r'''set -u
export LC_ALL=C
printf 'cann=%s\n' "$(readlink -f /usr/local/Ascend/cann)"
if [ -r /usr/local/Ascend/driver/version.info ]; then
    awk -F= 'tolower($1) ~ /^[[:space:]]*(version|driver_version|firmware_version|package_version)[[:space:]]*$/ {print}' /usr/local/Ascend/driver/version.info
fi
npu-smi info 2>/dev/null | awk 'NR <= 4 {print}'
python3 - <<'LINUX'
import json
from pathlib import Path
import re

root = Path.home()
patterns = ('*/matrix-*/retained-hccl-baseline.json', '*/*/hccl*baseline*.json')
files = sorted(set(path for pattern in patterns for path in root.glob(pattern)))
allowed = re.compile(r'(?:device|ndev|rank|host|topolog|mode|command|version|build|status|result|test|path|check|pass|covered|firmware|driver|cann|reason|port|cards)', re.I)
denied = re.compile(r'(?:secret|token|credential|password|private|stderr|stdout|raw)', re.I)

def limit(value, depth=0):
    if depth > 5:
        return None
    if isinstance(value, dict):
        return {str(k): limit(v, depth+1) for k, v in list(value.items())[:40]
                if allowed.search(str(k)) and not denied.search(str(k))}
    if isinstance(value, list):
        return [limit(v, depth+1) for v in value[:36]]
    return value[:360] if isinstance(value, str) else value

for path in files[:12]:
    try:
        data = json.loads(path.read_text())
        print('record', path, json.dumps(limit(data), ensure_ascii=True)[:2800])
    except (OSError, ValueError):
        continue
LINUX
for root in /usr/local/Ascend/cann /usr/local/Ascend/ascend-toolkit; do
    if [ -d "$root" ]; then
        resolved=$(readlink -f "$root")
        find "$resolved" -maxdepth 6 -type f -name all_reduce_test -perm /111 -print 2>/dev/null | head -4
    fi
done | sort -u
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
