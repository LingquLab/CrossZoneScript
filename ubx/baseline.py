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
npu-smi info 2>/dev/null | awk 'NR <= 4 {print} /^\|[[:space:]]+0[[:space:]]/ && !device0++ {print}'
npu-smi info -t board -i 0 -c 0 2>/dev/null | awk '/Firmware|Software Version|Board ID|NPU Name|Chip Name/ {print}' | head -10
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
python3 - <<'LINUX'
import hashlib
import json
from pathlib import Path
import re

home = Path.home()
for directory in sorted(home.glob('tilexr-ubx-validation-*/qualification-*')):
    print('qualification', str(directory))
    for name in ('environment.json', 'result.json', 'attempt.json'):
        file = directory / name
        if not file.is_file():
            continue
        data = json.loads(file.read_text())
        selected = {}
        for key, value in data.items():
            if re.search(r'device|rank|host|topolog|acceler|test|status|result|exit|build|driver|firmware|cann|binary|identity|classif|pass|fail|verdict|reason|attempted', key, re.I) and not re.search(r'log|raw|env$|out$', key, re.I):
                selected[key] = value
        print(name, json.dumps(selected)[:1800])
for file in sorted(home.glob('crosszone/TileXR-*/hccl-aiv-baseline/all_reduce.log')):
    print('baseline-log', str(file))
    print('bytes', file.stat().st_size)
    text = file.read_text(errors='replace')
    other = file.with_name('result.json')
    if other.is_file():
        result = json.loads(other.read_text())
        print('result', json.dumps({key: result[key] for key in ('status', 'classification', 'ranks', 'devices', 'return_code') if key in result}))
    print('capacity lines', [line.strip()[:120] for line in text.splitlines() if 'size' in line.lower()][:3])
    lines = [line.strip()[:230] for line in text.splitlines() if re.search(r'all_reduce|rankSize|world size|aiv_only|[0-9]+ ranks?|test result|error.*test|out of bounds|num.*devices|data check|verification', line, re.I)]
    for line in list(dict.fromkeys(lines))[:15]:
        print(line)
for p in (Path('/usr/local/mpich-3.2.1/bin/mpirun'), Path('/usr/bin/mpirun')):
    if p.is_file():
        print('mpi', str(p))
LINUX
tool=/usr/local/Ascend/cann/tools/hccl_test/bin/all_reduce_test
if test -x "$tool"; then
    (
        source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1
        export LD_LIBRARY_PATH=/usr/local/mpich-3.2.1/lib:/usr/local/mpich-3.2.1/lib64:$LD_LIBRARY_PATH
        echo '=== test help ==='
        timeout 5 "$tool" --help 2>&1 | head -38
    )
fi
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
