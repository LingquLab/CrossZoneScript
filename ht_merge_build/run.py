"""Fetch the fixed TileXR source and invoke the approved Linux build channel."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root.parent / "ht_t32_e2e"))
sys.path.insert(0, str(root.parent / "alltoall"))
from green_auth import askpass_environment
from ssh_transport import HOST

REVISION = "8882b28a4cc586f91ddcea171170bf9e304adc70"
REPOSITORY = "https://github.com/LingquLab/TileXR.git"

def report(stage, error):
    print(json.dumps({"stage": stage, "status": error}, separators=(",", ":")))
    return 1

def main():
    with tempfile.TemporaryDirectory(prefix="tilexr-ht-source-") as temporary:
        def git(*args):
            return subprocess.run(["git", "-C", temporary, *args], capture_output=True,
                                  timeout=150, check=True).stdout
        try:
            git("init", "-q")
            git("fetch", "--depth", "1", REPOSITORY, REVISION)
            if git("rev-parse", "FETCH_HEAD").decode().strip() != REVISION:
                return report("source", "revision_mismatch")
            tree = git("rev-parse", "FETCH_HEAD^{tree}").decode().strip()
            archive = git("archive", "--format=tar.gz", REVISION)
        except (OSError, subprocess.SubprocessError) as error:
            return report("source", type(error).__name__)

    request = {"revision": REVISION, "tree": tree,
               "source_sha256": hashlib.sha256(archive).hexdigest(),
               "source": base64.b64encode(archive).decode(),
               "build_script": (root / "build.sh").read_text()}
    remote = (root / "remote.py").read_text().rstrip()
    ssh = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
           "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1",
           "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no",
           HOST, "python3", "-"]
    code = remote + "\nmain(" + repr(request) + ")\n"
    try:
        with askpass_environment() as environment:
            proc = subprocess.run(ssh, input=code.encode(), env=environment,
                                  capture_output=True, timeout=1150)
    except (OSError, subprocess.SubprocessError) as error:
        return report("ssh", type(error).__name__)
    if proc.returncode:
        error = proc.stderr.decode(errors="replace").lower()
        for phrase in ("permission denied", "connection timed out", "connection refused"):
            if phrase in error:
                return report("ssh", phrase.replace(" ", "_"))
        return report("remote", "exit_" + str(proc.returncode))
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        return report("remote", "invalid_receipt")
    print(json.dumps(result, separators=(",", ":")))
    return 0  # The receipt carries the independent exit status of each target.

if __name__ == "__main__":
    sys.exit(main())
