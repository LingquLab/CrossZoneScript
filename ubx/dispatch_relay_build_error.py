"""Return bounded compiler diagnostics for the RELAY token-alignment build."""

import json
from pathlib import Path, PurePosixPath
import shlex
import sys

from dispatch_relay_setup import NAMESPACE
from stage_sources import connect, run_remote

SCRIPT = r'''
set -eu
cd "$1"
awk '
  /error: |note: in instantiation of|static_assert failed|assert failed/ {
    line=$0
    i=index(line, "/src/ep/kernels/")
    if (i > 0) line=substr(line, i+16)
    else if (line ~ /^\//) sub(/^.*\//, "", line)
    print substr(line, 1, 380)
    if (++count == 15) exit
  }
' build-tail-relay-align-off.log
'''

if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with connect(args["host"]) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / NAMESPACE
        status, output, _ = run_remote(client, "bash -s -- " + shlex.quote(str(root)), input_text=SCRIPT)
        if status:
            raise SystemExit(status)
        print(output)
