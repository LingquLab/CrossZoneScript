"""Stage a fixed TileXR revision and run its Memory SST guide in a fresh bind."""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
revision = args["revision"]
if not re.fullmatch("[0-9a-f]{40}", revision):
    raise SystemExit("invalid revision")
image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
work = Path(tempfile.mkdtemp(prefix="memory-unitest-regression-"))
repo = work / "repo"
staged = work / "staged"
ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
       "-i", str(Path.home() / ".ssh/id_ed25519_server"), "root@141.61.94.30"]


def run(label, command, input_data=None, cwd=None, timeout=150):
    with (work / (label + ".stderr.log")).open("wb") as log:
        result = subprocess.run(command, cwd=cwd, input=input_data, stdout=subprocess.PIPE,
                                stderr=log, timeout=timeout)
    (work / (label + ".stdout.log")).write_bytes(result.stdout)
    result.check_returncode()
    return result.stdout.decode("utf-8").strip()


def remote(label, command, input_data=None, timeout=150):
    return run(label, [*ssh, command], input_data, timeout=timeout)


assert remote("image", f"docker image inspect --format '{{{{.Id}}}} {{{{.Architecture}}}}' {image}") == image + " arm64"
run("init", ["git", "init", str(repo)])
run("fetch", ["git", "fetch", "--no-tags", "https://github.com/LingquLab/TileXR.git", revision], cwd=repo)
run("checkout", ["git", "checkout", "--detach", revision], cwd=repo)
assert run("revision", ["git", "rev-parse", "HEAD"], cwd=repo) == revision
run("stage", [sys.executable, "-B", "tools/collectives/memory_sst/memory_sst.py", "stage",
              "--revision", revision, "--output", str(staged)], cwd=repo)
bind = remote("bind", "mktemp -d /opt/tilexr-memory-refactor.XXXXXXXX")
assert re.fullmatch(r"/opt/tilexr-memory-refactor\.[A-Za-z0-9]{8}", bind)
name = "tilexr-memory-refactor-" + bind.rsplit(".", 1)[1]
print(json.dumps(dict(stage="staged", revision=revision, bind=bind, control_logs=str(work))), flush=True)
remote("source", shlex.join(["tar", "-xf", "-", "-C", bind]), (staged / "source.tar").read_bytes())
remote("source-lock", f"cat > {shlex.quote(bind + '/source-lock.json')}",
       (staged / "source-lock.json").read_bytes())
accept_command = shlex.join(["bash", bind + "/source/tools/collectives/memory_sst/accept-host.sh",
                            bind, name, image])
result = remote("accept", accept_command + '; code=$?; printf "\nACCEPT_EXIT=%s\n" "$code"', timeout=1800)
exit_status = int(result.rsplit("ACCEPT_EXIT=", 1)[1])
if exit_status:
    from sys import exit
    print(json.dumps(dict(stage="accept_failed", revision=revision, bind=bind, acceptance_exit=exit_status,
                          last_lines=result.splitlines()[-10:])))
    # The retained bind distinguishes build, qualify and stage errors.
    exit(exit_status)
probe = Path(__file__).with_name("verify_remote.py").read_bytes()
print(remote("verify", shlex.join(["python3", "-", bind, name, revision]), probe))
