"""Return bounded progress for EP32 campaign services and artifacts."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex
import sys

from stage_sources import connect, run_remote


def snapshots(host, namespace):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
        raise ValueError("invalid namespace")
    with connect(host) as client, client.open_sftp() as transfer:
        transfer.get_channel().settimeout(12)
        root = transfer.normalize(".") + "/.local/state/tilexr-ubx/" + namespace
        command = '''set -u
for unit in "$2-build" "$2-profile-build" "$2-queue" "$2-report"; do
    state=$(systemctl is-active "$unit" 2>/dev/null || true)
    printf '%s %s\n' "$unit" "$state"
done
cd "$1" || exit 2
for file in build.exit ../"${1##*/}"-profile/build.exit status.json report-status.json retry-status.json; do
    if [[ -s "$file" ]]; then printf '%s %s\n' "$file" "$(head -c 1600 "$file")"; fi
done
if [[ -d ready ]]; then printf 'ready '; ls -1 ready | paste -sd ' ' -; fi
if [[ -d runs ]]; then
    printf 'finished_cases='
    find runs -name report.json -type f | wc -l
fi
for file in build*.log; do
    if [[ ! -r "$file" ]]; then continue; fi
    hits=$(grep -Ei 'CMake Error|fatal error:|error:|FAILED:|not found|unbound variable|No such file' "$file" | cut -c1-200 | tail -3)
    if [[ -n "$hits" ]]; then printf '%s\n%s\n' "$file" "$hits"; fi
done
'''
        invocation = "bash -s -- " + shlex.quote(root) + " " + shlex.quote("tilexr-" + namespace)
        code, text, _ = run_remote(client, invocation, input_text=command)
        print(host + " " + ("ready" if code == 0 else "status_unavailable") + "\n" + text)


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=len(args["hosts"])) as pool:
        futures = [pool.submit(snapshots, host, args["namespace"]) for host in args["hosts"]]
        for future in futures:
            future.result()
