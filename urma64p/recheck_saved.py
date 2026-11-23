"""Recheck the saved functional dumps while preserving their on-disk inputs."""

import argparse
import base64
import json
import re
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
run_dir = args["run_dir"]
saved_modes = args.get("saved_modes", ["syncall", "nosync", "clock"])
if not re.fullmatch(r"/opt/urma64p-cases\.[a-z0-9_]{8}", run_dir):
    parser.error("invalid saved run directory")
if not saved_modes or set(saved_modes) - {"syncall", "nosync", "clock"}:
    parser.error("invalid saved schedules")
source = Path(__file__).with_name("saved-checker.py").read_bytes()
payload = base64.b64encode(source).decode("ascii")
image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
origin = "/home/h30059441/tilexr_sim_cc9_hapi_work"
generator = "/work/coord_runner/full-matrix-fdcf9d52-20261119/generator"
host_program = f"""import base64,subprocess,sys
argv = ['docker','run','--rm','-i','--network','none',
        '-v',{f'{origin}:/work:ro'!r},'-v',{f'{run_dir}:/run-cases:ro'!r},
        '-e',{f'PYTHONPATH={generator}'!r},'-e',{f'URMA_SAVED_MODES={",".join(saved_modes)}'!r},
        '--entrypoint','python3',{image!r},'-Bu','-']
result = subprocess.run(argv, input=base64.b64decode({payload!r}), timeout=810)
sys.exit(result.returncode)
"""
ssh = [
    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
    "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
    "root@141.61.94.30", "python3 -u -",
]
with Path("urma64p-recheck.stderr.log").open("wb") as errors:
    with subprocess.Popen(ssh, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors) as proc:
        proc.stdin.write(host_program.encode())
        proc.stdin.close()
        for line in proc.stdout:
            print(json.dumps(json.loads(line)), flush=True)
        raise SystemExit(proc.wait())
