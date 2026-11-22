"""Reuse the prepared Combine Write audit without rebuilding the fixture."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex
import sys

from stage_sources import connect, run_remote


def stage_runner(host, namespace):
    with connect(host) as client, client.open_sftp() as transfer:
        transfer.get_channel().settimeout(30)
        root = transfer.normalize(".") + "/.local/state/tilexr-ubx/" + namespace
        status = root + "/build.exit"
        with transfer.open(status) as result:
            if result.read().strip() != b"0":
                raise ValueError("audit build is unavailable")
        if run_remote(client, "test -d " + shlex.quote(root + "/diagnostic"))[0] == 0:
            return root, True
        runner = root + "/run_combine_audit.py"
        transfer.put(str(Path(__file__).with_name("run_combine_audit.py")), runner + ".part")
        transfer.posix_rename(runner + ".part", runner)
        return root, False


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace = args["namespace"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
        raise ValueError("invalid namespace")
    hosts = args["hosts"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        inputs = list(pool.map(lambda host: stage_runner(host, namespace), hosts))
    if any(already_exists for _, already_exists in inputs):
        print(json.dumps({"phase": "prior_attempt_exists", "paths": [root for root, _ in inputs]}))
        return
    root = inputs[0][0]
    wait_seconds = args.get("wait_seconds", 360)
    command = shlex.join(["/usr/bin/python3", root + "/run_combine_audit.py", "audit",
                          args["original"], hosts[0], args["peer"], str(wait_seconds)])
    with connect(hosts[0]) as client:
        code, output, _ = run_remote(client, command, timeout=wait_seconds + 255)
        if code:
            print(json.dumps({"phase": "audit_dispatch_failed", "exit": code}))
        else:
            print(output)


if __name__ == "__main__":
    main()
