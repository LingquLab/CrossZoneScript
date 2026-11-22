"""Read the targeted Combine audit's launch and rank state on each node."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex
import sys

from stage_sources import connect, run_remote

SCRIPT = r"""
set -u
cd "$1" || exit 2
unit="tilexr-${1##*/}-single"
printf 'service='
systemctl is-active "$unit" 2>/dev/null || true
if [[ -d diagnostic ]]; then
    printf 'rank_dirs=%s\n' "$(find diagnostic -maxdepth 1 -type d -name 'rank-*' | wc -l)"
    if [[ -s diagnostic/audit.json ]]; then head -c 1800 diagnostic/audit.json; printf '\n'; fi
    python3 - <<'INNER'
from collections import Counter
from pathlib import Path
import re

prefixes = ("UBX_FAILURE", "UBX_COMBINE_RUNTIME", "UBX_COMBINE_PASS", "UBX_COMBINE_AUDIT_COPY")
root = Path("diagnostic")
counts = Counter()
examples = {}
for folder in root.glob("rank-*"):
    seen = {}
    for name in ("stdout.log", "stderr.log"):
        path = folder / name
        if not path.is_file():
            continue
        for line in path.read_text(errors="replace")[-100000:].splitlines():
            if line.startswith(prefixes):
                label = line.split()[0]
                detail = re.sub(r"rank=\d+", "rank=<rank>", line).split(" detail=", 1)[0]
                seen[label] = detail[:220]
            elif re.search(r'error while loading|cannot open|fatal|return code|rendezvous|Connection refused', line, re.I):
                seen.setdefault("launcher", line.strip()[:200])
    for label, detail in seen.items():
        counts[label] += 1
        examples.setdefault(label, detail)
print(dict(counts))
for line in examples.values():
    print(line)
INNER
fi
journalctl -q -u "$unit" --no-pager -n 25 -o cat 2>/dev/null | \
    grep -Ei 'Traceback|File .*run_combine|Error|Failed|exit-code|SIG' | cut -c1-210 | tail -12
"""


def inspect(host, namespace):
    with connect(host) as client, client.open_sftp() as transfer:
        root = transfer.normalize(".") + "/.local/state/tilexr-ubx/" + namespace
        code, output, _ = run_remote(client, "bash -s -- " + shlex.quote(root), input_text=SCRIPT, timeout=30)
        return host, code, output


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args["namespace"]):
        raise ValueError("invalid namespace")
    with ThreadPoolExecutor(max_workers=2) as pool:
        for host, code, output in pool.map(lambda host: inspect(host, args["namespace"]), args["hosts"]):
            print(f"{host} exit={code}\n{output}")
