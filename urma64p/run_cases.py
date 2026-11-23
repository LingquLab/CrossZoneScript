"""Run Full AllToAll cases against an isolated server-30 SST container."""

import argparse
import json
import re
import shlex
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
build = args["build"]
kind = args["kind"]
size = args["size"]
timeout = args["timeout"]
deadline = args["deadline"]
schedules = args["schedules"]
if not re.fullmatch(r"/opt/urma64p-full\.[A-Za-z0-9]{6}", build):
    parser.error("invalid build directory")
if kind not in ("functional", "performance"):
    parser.error("invalid case type")
if size not in ("512b-per-peer", "512kib-per-rank", "4mib-per-rank", "16mib-per-rank"):
    parser.error("invalid payload size")
if not 1 <= len(schedules) <= 3 or any(s not in ("syncall", "clock", "nosync") for s in schedules):
    parser.error("invalid schedules")
if not 30 <= timeout <= 21600 or not 0 <= deadline < (1 << 63):
    parser.error("invalid timeout or clock deadline")
if "clock" in schedules and deadline == 0:
    parser.error("clock deadline required")
if kind == "functional" and size != "512kib-per-rank":
    parser.error("only the 512 KiB/rank functional reference is retained for 64P")

directory = Path(__file__).resolve().parent
ssh = [
    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
    "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
    "root@141.61.94.30",
]
remote = (directory / "remote-cases.py").read_text(encoding="utf-8")
prepare = (directory / "prepare_case.py").read_text(encoding="utf-8")
cmd = "python3 -u - " + shlex.quote(json.dumps(dict(args, prepare=prepare)))
with Path("urma64p-ssh.stderr.log").open("wb") as errors:
    with subprocess.Popen(ssh + [cmd], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors) as proc:
        proc.stdin.write(remote.encode())
        proc.stdin.close()
        for line in proc.stdout:
            print(json.dumps(json.loads(line)), flush=True)
        raise SystemExit(proc.wait())
