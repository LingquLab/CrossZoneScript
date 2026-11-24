"""Stage pinned TileXR sources and compile the Full kernel on the SST host."""

import argparse
import json
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
revision = args["revision"]
variant = args.get("variant", "two-group")
if not re.fullmatch(r"[0-9a-f]{40}", revision):
    parser.error("invalid revision")
if variant not in ("two-group", "clos-4plus8"):
    parser.error("invalid Full variant")

entry = Path(__file__).resolve().with_name("remote-build.sh")
remote_script = entry.read_text(encoding="utf-8")
ssh = [
    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
    "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
    "root@141.61.94.30",
]


def git(directory, *argv):
    return subprocess.run(
        ["git", "-C", directory, *argv], check=True, capture_output=True, timeout=90,
    ).stdout


stage = "fetch"
try:
    with tempfile.TemporaryDirectory(prefix="tilexr-full-") as directory:
        git(directory, "init", "--bare")
        git(directory, "fetch", "--depth=1", "--no-tags",
            "https://github.com/LingquLab/TileXR.git", revision)
        if git(directory, "rev-parse", "FETCH_HEAD").decode().strip() != revision:
            raise ValueError("source identity mismatch")
        stage = "archive"
        archive = git(directory, "archive", "--format=tar.gz", revision, "src")
        stage = "build"
        with Path("sst-host-build.stderr.log").open("wb") as errors:
            command = ssh + ["-T", "bash -c " + shlex.quote(remote_script) + " -- " + revision + " " + variant]
            try:
                result = subprocess.run(command, input=archive, stdout=subprocess.PIPE, stderr=errors, timeout=550)
            except subprocess.TimeoutExpired as exc:
                for line in (exc.stdout or b"").splitlines(keepends=True):
                    if line.endswith(b"\n"):
                        print(json.dumps(json.loads(line), ensure_ascii=True), flush=True)
                raise
    for line in result.stdout.decode().splitlines():
        print(json.dumps(json.loads(line), ensure_ascii=True))
    raise SystemExit(result.returncode)
except (OSError, ValueError, subprocess.SubprocessError) as exc:
    print(json.dumps({"stage": stage, "error": type(exc).__name__}))
    raise SystemExit(1)
