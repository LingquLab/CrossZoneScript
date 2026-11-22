"""Resume the requested ordered UBX matrix after archiving its failed first attempt."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys

from deploy import deploy
from stage_sources import connect, run_remote
from status import snapshots


def preserve(host, namespace):
    with connect(host) as client, client.open_sftp() as sftp:
        root = PurePosixPath(sftp.normalize(".")) / ".local/state/tilexr-ubx" / namespace
        script = r"""
set -eu
cd "$1"
if [[ -f attempts/initial-ubx/archived ]]; then exit 0; fi
if systemctl is-active --quiet "$2"; then exit 3; fi
mkdir -p attempts/initial-ubx
if [[ -d runs/stages-write-bs32-off ]]; then
    mv -- runs/stages-write-bs32-off attempts/initial-ubx/
fi
for file in blocked.json status.json report-status.json report.md; do
    if [[ -f "$file" ]]; then cp -- "$file" attempts/initial-ubx/; fi
done
touch attempts/initial-ubx/archived
"""
        unit = "tilexr-" + namespace + "-queue"
        command = "bash -s -- " + shlex.quote(str(root)) + " " + shlex.quote(unit)
        code, _, _ = run_remote(client, command, input_text=script)
        if code:
            raise RuntimeError(f"could not preserve the previous attempt on {host}")


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace = args["namespace"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
        raise ValueError("invalid namespace")
    coordinator, peer = args["hosts"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda host: preserve(host, namespace), (coordinator, peer)))
    config = {"namespace": namespace, "files": ["campaign.py"], "program": "campaign.py",
              "interpreter": "/usr/bin/python3", "unit": "queue", "start_host": coordinator,
              "argv": ["campaign", coordinator, args["peer"]]}
    print(json.dumps({"peer": deploy(peer, config)}), flush=True)
    print(json.dumps({"queue": deploy(coordinator, config)}), flush=True)
    reporter = {"namespace": namespace, "files": ["report.py"], "program": "report.py",
                "interpreter": "/usr/bin/python3", "unit": "report", "argv": [args["peer"]]}
    print(json.dumps({"report": deploy(coordinator, reporter)}), flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda host: snapshots(host, namespace), (coordinator, peer)))


if __name__ == "__main__":
    main()
