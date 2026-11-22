"""Deploy the two-cap matrix and detach retained-data recovery from the bridge."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import sys

from dispatch_abc_setup import MATRIX, NAMESPACE, stage_matrix
from install_keepalive import stage
from stage_sources import connect, run_remote


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    coordinator, peer = args["hosts"]
    if args["peer"] != "root@" + peer:
        raise ValueError("unexpected peer endpoint")
    with ThreadPoolExecutor(max_workers=2) as pool:
        files = ["dispatch_abc_recover.py", "tail_report.py", "campaign.py", "report.py"]
        roots = list(pool.map(lambda host: stage(host, NAMESPACE, files), args["hosts"]))
        list(pool.map(stage_matrix, args["hosts"]))
    unit = "tilexr-" + NAMESPACE + "-reconcile"
    command = ["systemd-run", "--quiet", "--collect", "--property=Type=exec",
               "--property=Restart=on-failure", "--property=RestartSec=15s", "--unit=" + unit,
               "/usr/bin/python3", str(roots[0] / "dispatch_abc_recover.py"), args["peer"]]
    with connect(coordinator) as client:
        status, _, _ = run_remote(client, shlex.join(command))
        if status:
            raise SystemExit(status)
        status, state, _ = run_remote(client, f"systemctl show {unit} -p ActiveState -p SubState")
        if status:
            raise SystemExit(status)
    print(json.dumps(dict(unit=unit, status=state.replace("\n", " "), cases=len(MATRIX),
                          max_tokens=[1, 2], batch_order=[64, 128, 32])))
