"""Inspect the first failing EP32 rank boundary and retained environment evidence."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex
import sys

from stage_sources import connect, run_remote


PROBE = r'''set -u
base=$1
case_name=$2
unit=tilexr-${base##*/}-$case_name
cd "$base" || exit 2
printf '%s\n' '=== service failure ==='
journalctl -q -u "$unit" --no-pager -n 35 -o cat 2>/dev/null |   grep -Ei 'Traceback|File .*campaign\.py|Error:|exit-code|Failed|SIG' | cut -c1-240 | tail -12
dir="$base/runs/$case_name"
if test -d "$dir"; then
  printf 'rank_dirs=%s\n' "$(find "$dir" -maxdepth 1 -type d -name 'rank-*' | wc -l)"
  printf 'rank_result_files=%s\n' "$(find "$dir" -iname '*round*.json' ! -iname '*.clock.json' | wc -l)"
  printf '%s\n' '=== first rank errors ==='
  grep -hEi 'failed|error|invalid|not found|unable|exited|507035|Traceback' "$dir"/rank-*/stderr.log "$dir"/rank-*/stdout.log 2>/dev/null |     cut -c1-220 | awk '!seen[$0]++' | head -24
fi
printf '%s\n' '=== retained baseline record filenames ==='
find "$HOME" /data /workspace -maxdepth 3 -type f \( -iname '*hccl*aiv*.log' -o -iname '*hccl*baseline*.json' -o -iname '*hccl*baseline*.log' -o -iname '*all_reduce_test*.log' \) -print 2>/dev/null | head -15
'''


def inspect(host, args):
    namespace = args["namespace"]
    case_name = args["case"]
    if any(re.fullmatch(r"[a-zA-Z0-9_-]+", value) is None for value in (namespace, case_name)):
        raise ValueError("invalid campaign identity")
    with connect(host) as client, client.open_sftp() as transfer:
        transfer.get_channel().settimeout(12)
        root = transfer.normalize(".") + "/.local/state/tilexr-ubx/" + namespace
        invocation = "bash -s -- " + shlex.quote(root) + " " + shlex.quote(case_name)
        code, text, _ = run_remote(client, invocation, timeout=30, input_text=PROBE)
    return f"{host} exit={code}\n{text}"


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=len(args["hosts"])) as pool:
        for result in pool.map(lambda host: inspect(host, args), args["hosts"]):
            print(result)
