"""Run Full AllToAll cases against an isolated server-30 SST container."""

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args_file = parser.parse_args().args_file
args = json.loads(args_file.read_text(encoding="utf-8"))
build = args["build"]
kind = args["kind"]
size = args["size"]
timeout = args["timeout"]
deadline = args["deadline"]
schedules = args["schedules"]
auto_clock = args.get("auto_clock", False)
if not re.fullmatch(r"/opt/urma64p-full\.[A-Za-z0-9]{6}", build):
    parser.error("invalid build directory")
if "expected_revision" in args and not re.fullmatch(r"[0-9a-f]{40}", args["expected_revision"]):
    parser.error("invalid expected revision")
if kind not in ("functional", "performance"):
    parser.error("invalid case type")
if size not in ("512b-per-peer", "512kib-per-rank", "4mib-per-rank", "16mib-per-rank"):
    parser.error("invalid payload size")
if not 1 <= len(schedules) <= 3 or any(s not in ("syncall", "clock", "nosync") for s in schedules):
    parser.error("invalid schedules")
if not 30 <= timeout <= 21600 or not 0 <= deadline < (1 << 63):
    parser.error("invalid timeout or clock deadline")
if "clock" in schedules and deadline == 0 and not auto_clock:
    parser.error("clock deadline required")
if kind == "functional" and size != "512kib-per-rank":
    parser.error("only the 512 KiB/rank functional reference is retained for 64P")

directory = Path(__file__).resolve().parent
if "run_dir" in args:
    result = subprocess.run([sys.executable, str(directory / "recheck_saved.py"),
                             "--args-file", str(args_file)], capture_output=True)
    for line in result.stdout.splitlines():
        evidence = json.loads(line)
        print(json.dumps(evidence), flush=True)
        if auto_clock and evidence.get("schedule") == "syncall" and "timing" in evidence:
            # Bootstrap with a small margin. The clock case independently checks separation.
            args["deadline"] = evidence["timing"]["last_same_end"] + 4096
    result.check_returncode()
if auto_clock and "clock" in schedules and args["deadline"] == 0 and "syncall" not in schedules:
    parser.error("automatic deadline requires verified SyncAll timing")
ssh = [
    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
    "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
    "root@141.61.94.30",
]
remote = (directory / "remote-cases.py").read_text(encoding="utf-8")
prepare = (directory / "prepare_case.py").read_bytes()
payload = json.dumps(dict(args, prepare_b64=base64.b64encode(prepare).decode("ascii")))
encoded = base64.b64encode(payload.encode()).decode("ascii")
bootstrap = f"import base64, json\nargs = json.loads(base64.b64decode({encoded!r}))\n"
with Path("urma64p-ssh.stderr.log").open("wb") as errors:
    with subprocess.Popen(ssh + ["python3 -u -"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors) as proc:
        proc.stdin.write((bootstrap + remote).encode())
        proc.stdin.close()
        for line in proc.stdout:
            print(json.dumps(json.loads(line)), flush=True)
        raise SystemExit(proc.wait())
