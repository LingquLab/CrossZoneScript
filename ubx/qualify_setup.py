"""Install and observe the one-time EP32 environment qualification."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex
import sys
import time

from deploy import deploy
from stage_sources import connect, run_remote


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    hosts = args["hosts"]
    coordinator = hosts[0]
    namespace = args["namespace"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
        raise ValueError("invalid campaign namespace")
    if not args.get("observe_only"):
        config = {"namespace": namespace, "files": ["qualify.py"],
                  "program": "qualify.py", "interpreter": "/usr/bin/python3",
                  "argv": ["run", coordinator, args["peer"]], "unit": "qualification",
                  "start_host": coordinator}
        with ThreadPoolExecutor(max_workers=2) as pool:
            for result in pool.map(lambda host: deploy(host, config), reversed(hosts)):
                print(json.dumps(result), flush=True)
    with connect(coordinator) as client:
        with client.open_sftp() as transfer:
            root = transfer.normalize(".") + "/.local/state/tilexr-ubx/" + namespace
        deadline = time.monotonic() + args.get("wait_seconds", 900)
        last = None
        while time.monotonic() < deadline:
            code, output, _ = run_remote(
                client, "cat -- " + shlex.quote(root + "/qualification-status.json"), timeout=10,
            )
            if code == 0:
                result = json.loads(output)
                if result != last:
                    print(json.dumps(result), flush=True)
                    last = result
                if result["phase"] not in ("waiting_32p", "running"):
                    break
            time.sleep(12)
