"""Run the trace renderer against read-only case mounts on the SST server."""

import base64
import json
import subprocess
from pathlib import Path

from render_core_traces import CASES


def main():
    source = Path(__file__).with_name("render_core_traces.py").read_bytes()
    suffixes = [suffix for modes in CASES.values() for suffix in modes.values()]
    remote = f"""
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
origin = Path("/home/h30059441/tilexr_sim_cc9_hapi_work")
coord = origin / "coord_runner"
output = Path(tempfile.mkdtemp(prefix="urma64p-core-traces-", dir=coord))
command = ["docker", "run", "--rm", "-i", "--network", "none",
           "-v", f"{{origin}}:/work:ro", "-v", f"{{output}}:/output",
           "-e", "PYTHONPATH=/work/coord_runner/full-matrix-fdcf9d52-20261119/generator"]
for suffix in {suffixes!r}:
    case = Path("/opt") / ("urma64p-cases." + suffix)
    assert case.is_dir(), suffix
    command.extend(["-v", f"{{case}}:/profiles/{{suffix}}:ro"])
command.extend(["--entrypoint", "python3", image, "-Bu", "-"])
with (output / "renderer.stderr.log").open("wb") as log:
    run = subprocess.run(command, input=base64.b64decode({base64.b64encode(source).decode()!r}),
                         stderr=log, timeout=700)
if run.returncode:
    print(json.dumps(dict(stage="renderer_failed", exit=run.returncode, directory=str(output))), flush=True)
    raise SystemExit(1)
assert len(list(output.glob("*.trace.json"))) == 4 and (output / "64p-urma-core-traces.zip").is_file()
owner = origin.stat()
for item in output.iterdir():
    os.chown(item, owner.st_uid, owner.st_gid)
    item.chmod(0o640)
os.chown(output, owner.st_uid, owner.st_gid)
output.chmod(0o750)
print(json.dumps(dict(stage="traces_ready", directory=str(output))), flush=True)
"""
    ssh = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
        "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
        "root@141.61.94.30", "python3 -u -",
    ]
    with Path("urma64p-trace-ssh.stderr.log").open("wb") as errors:
        result = subprocess.run(ssh, input=remote.encode(), stderr=errors, timeout=730)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
