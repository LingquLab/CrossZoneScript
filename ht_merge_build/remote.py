"""Build fixed TileXR source in a private CPU-only container."""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

IMAGE = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
MOUNT = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54")

def main(request):
    result = {key: request[key] for key in ("revision", "tree", "source_sha256")}
    stage = "preflight"
    try:
        subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True,
                       check=True, timeout=15)
        stage = "prepare"
        work = Path(tempfile.mkdtemp(prefix="ht-build-8882b28a-", dir=MOUNT))
        result["build_record"] = work.name
        source = work / "source"
        source.mkdir()
        archive = base64.b64decode(request["source"], validate=True)
        assert hashlib.sha256(archive).hexdigest() == request["source_sha256"]
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as stream:
            stream.extractall(source, filter="data")
        (work / "build.sh").write_text(request["build_script"])
        stage = "compile"
        name = "tilexr-ht-" + work.name
        try:
            with (work / "container.log").open("wb") as log:
                proc = subprocess.run([
                    "docker", "run", "--rm", "--name", name, "--runtime=runc", "--network=none",
                    "--cap-drop=ALL", "--security-opt=no-new-privileges", "--cpus=8", "--memory=32g",
                    "--pids-limit=1024", "--mount", f"type=bind,src={work},dst=/work", IMAGE,
                    "/bin/bash", "/work/build.sh",
                ], stdout=log, stderr=subprocess.STDOUT, timeout=1040)
            result["container_exit"] = proc.returncode
        finally:
            running = subprocess.run(["docker", "inspect", name], capture_output=True, timeout=15)
            if running.returncode == 0:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        status = work / "build-summary.json"
        if status.is_file():
            result.update(json.loads(status.read_bytes()))
        else:
            result["status"] = "missing_build_summary"
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        result.update(stage=stage, status=type(error).__name__)
    print(json.dumps(result, separators=(",", ":")))
