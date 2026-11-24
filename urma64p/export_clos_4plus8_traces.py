"""Export verified 16P core traces beside the SST campaign records."""

import base64
import json
import subprocess
from pathlib import Path


REMOTE = r'''
import base64
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

origin = Path("/home/h30059441/tilexr_sim_cc9_hapi_work")
image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
assert subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                               text=True, timeout=15).strip() == image
with tempfile.TemporaryDirectory(prefix="urma16p-trace-src.", dir="/opt") as temp:
    for name, data in sources.items():
        (Path(temp) / name).write_bytes(base64.b64decode(data, validate=True))
    output = Path(tempfile.mkdtemp(prefix="urma16p-4plus8-core-traces-", dir=origin / "coord_runner"))
    command = [
        "docker", "run", "--rm", "--network", "none",
        "-v", f"{origin}:/work:ro", "-v", f"{temp}:/renderer:ro", "-v", f"{output}:/output",
        "-v", "/opt/urma16p-4plus8.38r7oidh:/functional16:ro",
        "-v", "/opt/urma16p-4plus8.v1gsudkw:/regression:ro",
        "-e", "PYTHONPATH=/work/coord_runner/full-matrix-fdcf9d52-20261119/generator",
        "--entrypoint", "python3", image, "-Bu", "/renderer/render_clos_4plus8_traces.py",
    ]
    log = output / "renderer.stderr.log"
    with log.open("wb") as errors:
        run = subprocess.run(command, stderr=errors, timeout=360)
    if run.returncode:
        lines = log.read_text(errors="replace").splitlines()
        issue = next((line[:230] for line in reversed(lines)
                      if re.match(r"\w+(Error|Exception):", line)), None)
        print(json.dumps(dict(stage="renderer_failed", error=issue, directory=str(output))), flush=True)
    else:
        assert (output / "16p-clos-4plus8-core-traces.zip").is_file()
        assert len(list(output.glob("*.trace.json"))) == 6
        owner = origin.stat()
        for item in output.glob("16p-clos-4plus8-*"):
            os.chown(item, owner.st_uid, owner.st_gid)
            item.chmod(0o640)
        os.chown(output, owner.st_uid, owner.st_gid)
        output.chmod(0o750)
        print(json.dumps(dict(stage="traces_ready", directory=str(output))), flush=True)
'''


def main():
    root = Path(__file__).resolve().parent
    sources = {name: base64.b64encode((root / name).read_bytes()).decode("ascii")
               for name in ("render_clos_4plus8_traces.py", "render_core_traces.py")}
    ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
           "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
           "root@141.61.94.30", "python3 -u -"]
    with Path("urma16p-trace-ssh.stderr.log").open("wb") as errors:
        result = subprocess.run(ssh, input=(f"sources = {sources!r}\n" + REMOTE).encode(),
                                stderr=errors, timeout=390)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
