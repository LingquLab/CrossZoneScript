"""Deploy campaign scripts through validated password-authenticated SSH."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys

from stage_sources import connect, run_remote


def deploy(host, args):
    namespace = args["namespace"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
        raise ValueError("invalid namespace")
    with connect(host) as client, client.open_sftp() as transfer:
        transfer.get_channel().settimeout(30)
        anchor = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / namespace
        target = shlex.quote(str(anchor))
        if run_remote(client, "umask 077; mkdir -p -- " + target)[0]:
            raise RuntimeError("cannot create campaign directory")
        for name in args["files"]:
            if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name):
                raise ValueError("invalid file")
            remote = str(anchor / name)
            transfer.put(str(Path(__file__).with_name(name)), remote + ".part", confirm=True)
            transfer.chmod(remote + ".part", 0o600)
            transfer.posix_rename(remote + ".part", remote)
        if host != args.get("start_host", host):
            return {"path": str(anchor), "state": "files staged"}
        paths = [shlex.quote(value) for value in args.get("argv", [])]
        program = shlex.quote(str(anchor / args["program"]))
        unit = shlex.quote("tilexr-" + namespace + "-" + args["unit"])
        command = (
            "set -eu; if ! systemctl is-active --quiet " + unit + "; then "
            + "systemd-run --quiet --collect --property=Type=exec --unit=" + unit
            + " " + args["interpreter"] + " " + program + " " + " ".join(paths)
            + "; fi; systemctl show -p ActiveState -p SubState -p MainPID " + unit
        )
        code, out, _ = run_remote(client, command)
        if code:
            raise RuntimeError("system service submission failed")
        return {"path": str(anchor), "unit": unit, "state": out.replace("\n", " ")}


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if args["interpreter"] not in ("/bin/bash", "/usr/bin/python3"):
        raise ValueError("unsupported interpreter")
    with ThreadPoolExecutor(max_workers=len(args["hosts"])) as pool:
        tasks = [pool.submit(deploy, host, args) for host in args["hosts"]]
        for host, task in zip(args["hosts"], tasks):
            try:
                print(json.dumps({"host": host, **task.result()}))
            except Exception as exc:
                print(json.dumps({"host": host, "outcome": type(exc).__name__}))


if __name__ == "__main__":
    main()
