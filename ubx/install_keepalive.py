"""Keep the EP32 occupancy queue independent of Bridge and worker process lifetime."""

import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys

from stage_sources import connect, run_remote


def stage(host, namespace, files):
    with connect(host) as client, client.open_sftp() as transfer:
        transfer.get_channel().settimeout(20)
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / namespace
        for filename in files:
            target = str(root / filename)
            transfer.put(str(Path(__file__).with_name(filename)), target + ".part")
            transfer.chmod(target + ".part", 0o600)
            transfer.posix_rename(target + ".part", target)
        return root


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    name = args["namespace"]
    coordinator, peer = args["hosts"]
    endpoint = args["peer"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name) or not re.fullmatch(r"[-\w]+@[\d.]+", endpoint):
        raise ValueError("invalid deployment arguments")
    stage(peer, name, ["campaign.py"])
    root = stage(coordinator, name, ["campaign.py", "report.py", "keepalive.py"])
    unit = "tilexr-" + name + "-recovery"
    if not re.fullmatch(r"[-\w./]+", str(root)):
        raise ValueError("invalid installation path")
    service = "[Unit]\nDescription=Resume the TileXR EP32 test services\nAfter=network-online.target\n\n"
    service += "[Service]\nType=oneshot\n"
    service += f"ExecStart=/usr/bin/python3 {root}/keepalive.py {coordinator} {endpoint}\n"
    timer = "[Unit]\nDescription=Check the TileXR EP32 queue every thirty seconds\n\n"
    timer += "[Timer]\nOnBootSec=30s\nOnUnitActiveSec=30s\nAccuracySec=1s\n\n"
    timer += "[Install]\nWantedBy=timers.target\n"
    setup = f"""
set -eu
umask 022
cat > /etc/systemd/system/{unit}.service <<'SERVICE'
{service}SERVICE
cat > /etc/systemd/system/{unit}.timer <<'TIMER'
{timer}TIMER
systemctl daemon-reload
systemctl enable --now {unit}.timer
systemctl show {unit}.timer -p ActiveState -p SubState -p UnitFileState
"""
    with connect(coordinator) as client:
        code, out, _ = run_remote(client, "bash -s", input_text=setup)
        if code:
            raise SystemExit(code)
        print(out)
