"""Inspect the Combine read attempt for complete rank data and transport failures."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex
import sys

from direct_results import query
from stage_sources import connect, run_remote


EXAMINE = r'''set -u
cd "$1" || exit 2
case="$2"
unit="tilexr-${1##*/}-$case"
journalctl -q -u "$unit" -u "tilexr-${1##*/}-queue" --no-pager     --since "2026-11-22 04:15:00 UTC" -n 80 -o cat 2>/dev/null |     grep -Ei 'ssh:|connection|timed out|broken pipe|Failed|status=|Traceback|ValueError|RuntimeError'     | cut -c1-185 | awk '!seen[$0]++' | tail -12 || true
shopt -s nullglob
dir="runs/$case"
printf 'node_summaries=%s\n' "$(find "$dir" -maxdepth 1 -name 'node-*.json' 2>/dev/null | wc -l)"
printf 'rank_round_summaries=%s\n' "$(find "$dir" -name 'combine-read-rank-*-round-*.json' ! -name '*.clock.json' 2>/dev/null | wc -l)"
printf 'dispatch_rank_round_summaries=%s\n' "$(find "$dir" -name '*.summary.json' 2>/dev/null | wc -l)"
printf 'dispatch_packet_captures=%s\n' "$(find "$dir" -name '*.records.bin' 2>/dev/null | wc -l)"
if [[ -d "$dir" ]]; then
    grep -hE 'UBX_FAILURE|COMBINE_FAILURE|507035|COMBINE_FAIL' "$dir"/rank-*/*.log 2>/dev/null       | cut -c1-165 | awk '!seen[$0]++' | head -5 || true
fi
'''


def inspect(host, args, coordinator):
    status = query(host, args["namespace"], coordinator)
    with connect(host) as client, client.open_sftp() as sftp:
        base = sftp.normalize(".") + "/.local/state/tilexr-ubx/" + args["namespace"]
        invocation = "bash -s -- " + shlex.quote(base) + " " + shlex.quote(args["case"])
        _, output, _ = run_remote(client, invocation, timeout=25, input_text=EXAMINE)
    return dict(host=host, **status, attempt=output)


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", args[name]) for name in ("namespace", "case")):
        raise ValueError("invalid test identity")
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(inspect, host, args, index == 0)
                for index, host in enumerate(args["hosts"])]
        for job in jobs:
            print(json.dumps(job.result(), separators=(",", ":")))
