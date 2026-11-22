"""Launch the same Combine-only audit from independently authenticated nodes."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
from pathlib import Path
import re
import shlex
import sys
import time
import uuid

from stage_sources import connect, run_remote


def execute(client, argv, timeout=20):
    return run_remote(client, shlex.join(argv), timeout=timeout)


def ready(client, root, original, check_unit):
    script = r"""
set -eu
if systemctl is-active --quiet "$3"; then
    printf 'other-queue-active '
fi
test -s "$1/build.exit"
test "$(cat "$1/build.exit")" = 0
/usr/bin/python3 "$2/campaign.py" probe "$2/ready/stages-off"
"""
    code, output, _ = run_remote(client, "bash -s -- " + shlex.join([root, original, check_unit]),
                                  timeout=18, input_text=script)
    return output if code == 0 else ""


def retrieve(client, root, unit, attempt):
    invocation = ["cat", root + "/" + attempt + "/audit.json"]
    code, result, _ = execute(client, invocation)
    if code == 0:
        return json.loads(result)
    _, journal, _ = execute(client, ["journalctl", "-q", "-u", unit, "-n", "7", "-o", "cat", "--no-pager"])
    errors = [line[:200] for line in journal.splitlines() if re.search(r"Traceback|Error|failed|signal|exit", line, re.I)]
    return {"audit_rows": 0, "service_errors": errors}


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace, original = args["namespace"], args["original"]
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", name) for name in (namespace, original)):
        raise ValueError("invalid campaign identity")
    hosts = args["hosts"]
    audit_file = Path(__file__).with_name("run_combine_audit.py")
    attempt = "diagnostic-" + uuid.uuid4().hex[:12]
    unit = f"tilexr-{namespace}-{attempt}"
    with ExitStack() as cleanup, ThreadPoolExecutor(max_workers=2) as pool:
        clients = [cleanup.enter_context(connect(host)) for host in hosts]
        roots = []
        for client in clients:
            with client.open_sftp() as transfer:
                transfer.get_channel().settimeout(25)
                directory = transfer.normalize(".") + "/.local/state/tilexr-ubx/"
                root = directory + namespace
                original_root = directory + original
                runner = root + "/run_combine_audit.py"
                transfer.put(str(audit_file), runner + ".part")
                transfer.posix_rename(runner + ".part", runner)
                roots.append((root, original_root))
        deadline = time.monotonic() + args.get("wait_seconds", 360)
        existing_unit = "tilexr-" + original + "-queue"
        while True:
            futures = [pool.submit(ready, client, root, old, existing_unit)
                       for client, (root, old) in zip(clients, roots)]
            states = [future.result() for future in futures]
            if states == ["1 " + "I" * 16] * 2:
                break
            if time.monotonic() >= deadline:
                print(json.dumps({"phase": "devices_unavailable", "states": states}))
                return
            time.sleep(5)
        find_port = "import socket,sys; s=socket.socket(); s.bind((sys.argv[1],0)); print(s.getsockname()[1])"
        code, port, _ = execute(clients[0], ["/usr/bin/python3", "-c", find_port, hosts[0]])
        if code or not port.isdigit():
            raise RuntimeError("cannot allocate diagnostic rendezvous")
        rendezvous = f"{hosts[0]}:{port}"
        capture = uuid.uuid4().hex
        starts = []
        for index, (client, (root, _)) in enumerate(zip(clients, roots)):
            argv = ["systemd-run", "--quiet", "--collect", "--property=Type=exec",
                    "--property=RuntimeMaxSec=175", "--unit=" + unit,
                    "/usr/bin/python3", root + "/run_combine_audit.py", "node", original,
                    str(index), rendezvous, capture, attempt]
            starts.append(pool.submit(execute, client, argv, 30))
        launched = [call.result()[0] for call in starts]
        print(json.dumps({"launch_codes": launched, "attempt": attempt}), flush=True)
        if any(launched):
            for client, code in zip(clients, launched):
                if code == 0:
                    execute(client, ["systemctl", "stop", unit])
            return
        until = time.monotonic() + 192
        while time.monotonic() < until:
            checks = [pool.submit(execute, client, ["systemctl", "is-active", unit]) for client in clients]
            if all(check.result()[1] in ("inactive", "failed") for check in checks):
                break
            time.sleep(5)
        for host, client, (root, _) in zip(hosts, clients, roots):
            print(json.dumps({"host": host, **retrieve(client, root, unit, attempt)}, separators=(",", ":")))


if __name__ == "__main__":
    main()
