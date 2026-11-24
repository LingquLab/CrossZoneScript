"""Build and run Memory AllToAll cases in a fresh server-30 SST container."""
import argparse
import base64
import json
from pathlib import Path
import re
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
assert set(args) == {"revision", "main"}
assert all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in args.values())
scripts = Path(__file__).resolve().parent

with tempfile.TemporaryDirectory(prefix="tilexr-memory-sst-") as repository:
    def git(*arguments):
        return subprocess.check_output(["git", "-C", repository, *arguments],
                                       stderr=subprocess.PIPE, timeout=120)
    git("init", "--bare")
    git("fetch", "--no-tags", "https://github.com/LingquLab/TileXR.git",
        "refs/heads/alltoall_sim_mem:refs/heads/alltoall_sim_mem")
    assert git("rev-parse", "alltoall_sim_mem").decode().strip() == args["revision"]
    git("merge-base", "--is-ancestor", args["main"], args["revision"])
    archive = git("archive", "--format=tar.gz", args["revision"], "include", "src",
                  "cmake", "tools/unitest", "tests/collectives/memory", "tests/relay/mock")

runners = {
    "memory": scripts.parent / "alltoall/case_remote.py",
    "clock": scripts.parent / "clock_group/case_remote.py",
    "boundary": scripts.parent / "group_schedule/case_remote.py",
}
program = "args = " + repr(dict(args, batch="final-smoke")) + "\n"
program += "archive = " + repr(base64.b64encode(archive).decode("ascii")) + "\n"
program += "build_source = " + repr((scripts / "build_remote.py").read_text(encoding="utf-8")) + "\n"
program += "run_source = " + repr((scripts / "run_remote.py").read_text(encoding="utf-8")) + "\n"
program += "runners = " + repr({key: path.read_text(encoding="utf-8")
                                 for key, path in runners.items()}) + "\n"
program += (scripts / "smoke_host.py").read_text(encoding="utf-8")
command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
           "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
           "root@141.61.94.30", "python3 -u -"]
with Path("memory-sst-ssh.stderr.log").open("wb") as diagnostics:
    result = subprocess.run(command, input=program.encode(), stdout=subprocess.PIPE,
                            stderr=diagnostics, timeout=7350)
for line in result.stdout.splitlines():
    print(json.dumps(json.loads(line)), flush=True)
raise SystemExit(result.returncode)
